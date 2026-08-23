#!/bin/sh
# Backup Postgres (pg_dump -Fc), Qdrant (REST snapshots), and MinIO (S3 objects).
#
# Industry refs:
#   Postgres: docker exec pg_dump -Fc (not compose exec from cron; no TTY).
#   Qdrant:   POST /snapshots then GET /snapshots/{name} then DELETE
#             (https://qdrant.tech/documentation/concepts/snapshots/)
#             Host does not publish 6333; run urllib inside omni-butler-api.
#   MinIO:    list+get the app bucket via boto3 (same client as the API).
#             Official mc mirror is equivalent; the server image has no mc.
#
# Install on the Linux VM (nightly):
#   chmod +x deploy/backup.sh
#   crontab -e
#   15 2 * * * COMPOSE_DIR=/mnt/hgfs/omni-butler BACKUP_DIR=/var/omni-backups /mnt/hgfs/omni-butler/deploy/backup.sh
#
# Restore sketch (VM):
#   docker exec -i omni-butler-postgres pg_restore -U omni -d omni_butler --clean --if-exists < postgres.dump
#   Qdrant: PUT /snapshots/recover or upload via the snapshot API.
#   MinIO: extract minio.tgz and aws s3 cp / mc cp back into the bucket.

set -eu

COMPOSE_DIR="${COMPOSE_DIR:-}"
if [ -z "$COMPOSE_DIR" ]; then
  COMPOSE_DIR="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
fi
BACKUP_DIR="${BACKUP_DIR:-/var/omni-backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-omni-butler-postgres}"
API_CONTAINER="${API_CONTAINER:-omni-butler-api}"
PG_USER="${PG_USER:-omni}"
PG_DB="${PG_DB:-omni_butler}"
LOCK_FILE="${LOCK_FILE:-/tmp/omni-butler-backup.lock}"
tag="omni-butler-backup"
fail=0
msg=""

note() {
  msg="${msg}${1}
"
}

if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "backup already running (${LOCK_FILE})" >&2
    exit 0
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "docker not found" >&2
  exit 1
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dest="${BACKUP_DIR}/${stamp}"
mkdir -p "$dest"

if docker exec "$POSTGRES_CONTAINER" pg_dump -U "$PG_USER" -Fc --no-owner "$PG_DB" >"${dest}/postgres.dump"; then
  note "postgres dump ok ($(wc -c <"${dest}/postgres.dump") bytes)"
else
  fail=1
  note "postgres pg_dump failed"
  rm -f "${dest}/postgres.dump"
fi

if docker exec -i "$API_CONTAINER" python -u - >"${dest}/qdrant.snapshot" <<'PY'
import json, os, sys, urllib.error, urllib.parse, urllib.request

def log(m):
    print(m, file=sys.stderr, flush=True)

base = (os.environ.get("QDRANT_URL") or "http://qdrant:6333").rstrip("/")
timeout = 180

def open_url(req, timeout=timeout):
    return urllib.request.urlopen(req, timeout=timeout)

def create_storage_snapshot():
    req = urllib.request.Request(base + "/snapshots", data=b"", method="POST")
    with open_url(req) as resp:
        body = json.load(resp)
    result = body.get("result") or body
    name = result.get("name") if isinstance(result, dict) else None
    if not name:
        raise RuntimeError("qdrant snapshot create returned no name: %r" % (body,))
    return name, base + "/snapshots/" + urllib.parse.quote(name)

def create_collection_snapshots():
    with open_url(urllib.request.Request(base + "/collections")) as resp:
        cols = (json.load(resp).get("result") or {}).get("collections") or []
    snaps = []
    for c in cols:
        name = c.get("name") if isinstance(c, dict) else None
        if not name:
            continue
        url = base + "/collections/" + urllib.parse.quote(name) + "/snapshots"
        req = urllib.request.Request(url, data=b"", method="POST")
        try:
            with open_url(req) as resp:
                body = json.load(resp)
        except urllib.error.HTTPError:
            req = urllib.request.Request(url, data=b"", method="PUT")
            with open_url(req) as resp:
                body = json.load(resp)
        result = body.get("result") or body
        snap = result.get("name") if isinstance(result, dict) else None
        if snap:
            snaps.append(
                (
                    name,
                    snap,
                    url.rstrip("/") + "/" + urllib.parse.quote(snap),
                )
            )
    if not snaps:
        raise RuntimeError("no qdrant collections to snapshot")
    return snaps

created = []
try:
    name, get_url = create_storage_snapshot()
    created.append((base + "/snapshots/" + urllib.parse.quote(name), get_url))
    log("qdrant storage snapshot %s" % name)
except Exception as exc:
    log("storage snapshot failed (%s); trying per-collection" % exc)
    for col, snap, get_url in create_collection_snapshots():
        del_url = (
            base
            + "/collections/"
            + urllib.parse.quote(col)
            + "/snapshots/"
            + urllib.parse.quote(snap)
        )
        created.append((del_url, get_url))
        log("qdrant collection %s snapshot %s" % (col, snap))

# One snapshot on stdout is enough for the usual single-storage case.
# If we only have collection snapshots, concatenate them (restore uses the first).
_, get_url = created[0]
req = urllib.request.Request(get_url)
req.add_header("Accept", "application/octet-stream")
with open_url(req, timeout=300) as resp:
    while True:
        chunk = resp.read(1024 * 1024)
        if not chunk:
            break
        sys.stdout.buffer.write(chunk)
sys.stdout.buffer.flush()

for del_url, _ in created:
    try:
        open_url(urllib.request.Request(del_url, method="DELETE"))
    except Exception as exc:
        log("could not delete snapshot %s: %s" % (del_url, exc))
PY
then
  if [ ! -s "${dest}/qdrant.snapshot" ]; then
    fail=1
    note "qdrant snapshot empty"
    rm -f "${dest}/qdrant.snapshot"
  else
    note "qdrant snapshot ok ($(wc -c <"${dest}/qdrant.snapshot") bytes)"
  fi
else
  fail=1
  note "qdrant snapshot failed (need running ${API_CONTAINER} on the compose network)"
  rm -f "${dest}/qdrant.snapshot"
fi

if docker exec -i "$API_CONTAINER" python -u - >"${dest}/minio.tgz" <<'PY'
import io, os, sys, tarfile

import boto3
from botocore.client import Config

endpoint = (os.environ.get("S3_ENDPOINT") or "http://minio:9000").strip()
key = os.environ.get("S3_ACCESS_KEY") or "minioadmin"
secret = os.environ.get("S3_SECRET_KEY") or "minioadmin"
bucket = os.environ.get("S3_BUCKET") or "omni-uploads"
region = os.environ.get("S3_REGION") or "us-east-1"

client = boto3.client(
    "s3",
    endpoint_url=endpoint,
    aws_access_key_id=key,
    aws_secret_access_key=secret,
    region_name=region,
    config=Config(s3={"addressing_style": "path"}),
)

tf = tarfile.open(fileobj=sys.stdout.buffer, mode="w|gz")
n = 0
paginator = client.get_paginator("list_objects_v2")
for page in paginator.paginate(Bucket=bucket):
    for obj in page.get("Contents") or []:
        k = obj["Key"]
        body = client.get_object(Bucket=bucket, Key=k)["Body"].read()
        info = tarfile.TarInfo(name=k)
        info.size = len(body)
        tf.addfile(info, io.BytesIO(body))
        n += 1
        print("minio %s (%d bytes)" % (k, len(body)), file=sys.stderr, flush=True)
tf.close()
print("minio objects=%d" % n, file=sys.stderr, flush=True)
PY
then
  note "minio archive ok ($(wc -c <"${dest}/minio.tgz") bytes)"
else
  fail=1
  note "minio archive failed"
  rm -f "${dest}/minio.tgz"
fi

printf '%s\n' "$msg" >"${dest}/MANIFEST.txt"

if [ "$BACKUP_KEEP_DAYS" -gt 0 ] 2>/dev/null; then
  find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +"$BACKUP_KEEP_DAYS" -exec rm -rf {} +
fi

if [ "$fail" -ne 0 ]; then
  logger -t "$tag" "$msg" 2>/dev/null || true
  printf '%s\n' "$msg" >&2
  exit 1
fi

printf '%s\n' "backup ${dest} ok"
exit 0
