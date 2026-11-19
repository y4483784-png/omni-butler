# ev-picture review

A chat user can attach a JPEG, PNG, GIF, or WebP image. The bytes go to the configured chat role as `image_url` data URLs. The default chat model stays `deepseek-flash`. The path does not call `ocr_image_bytes`. `Message.content` stays text. One provider response writes one `LlmCall`. A missing token count stays null. A transport failure with no response writes no row. A response that is truncated, filtered, or stopped still stores the counts the provider returned.

## What the code does

`POST /api/chat` accepts `images`, a list of `media_type` and `data_base64`. Decode uses standard base64 with validation. The declared type must be one of the four media types, and the bytes must start with that signature. JPEG starts `FF D8`. PNG starts `89 50 4E 47`. GIF starts `GIF87a` or `GIF89a`. WebP starts with `RIFF` and has `WEBP` at offset 8. A byte length above `max_upload_mb * 1024 * 1024` or above 32 MiB is rejected. A length equal to either cap is allowed. A bad image returns 400 with `detail` set to `media_type`, `base64`, `signature`, or `size`.

A regenerate that also carries images returns 400 `detail` `regenerate` before the previous turn is replaced.

The current chat request does not call `bind_images`. The bytes are used for that request and then discarded. `bind_images` is the seam a later job calls. It stores one `task_images` row per image and puts the bytes through `BlobStore.put` under a key that starts with `task-image/`. That key is not written to `documents.stored_path`, and the browser does not receive it. `load_image_blocks` rebuilds the `image_url` blocks for the owning user while the job is running, including from a new database session. Another user gets `hidden`. A missing blob while the job is still running returns `unavailable` and does not call OCR.

A move to `completed` or `stopped` sets `purge_state` from `ready` or `staged` to `pending_delete` in the same transaction as the status write. That transaction does not call `BlobStore.delete`. After the commit, purge deletes the blob and then the row. A delete that fails, or that leaves the blob in place, keeps the row at `pending_delete`. A blob that is already absent still drops the row. The terminal status stays. `begin_turn`, `stop`, and `finish` run that purge for the same user after their own commit. The watchdog beat calls `reconcile_task_images` every 60 seconds for every tenant, so a pending row does not wait for that user to chat again. A purge error is swallowed so the existing stop and disconnect path still returns.

On Python 3.12, `break` inside `async for` does not close the async generator. The chat loop closes the stream after the existing stop check. Closing records the usage row. It does not yield another token, and it does not skip `task_run.stop`. Later units still change stop.

The measured body is compact UTF-8 JSON. `extra_body` is merged in. `timeout` is left out. `stream_options` counts when the call sets it. The installed OpenAI 2.54.0 client serializes with `openapi_dumps`, which uses `ensure_ascii=False` and separators `( ",", ":" )`. That was read from the installed package. No request was sent.

Mock mode writes no `LlmCall`. `complete_text` and `stream_chat` write one row after a provider response is in hand, including `finish_reason` `length` or `content_filter`, and including a response that arrives after the turn was stopped. A missing count stays null. A real zero stays 0. A create that throws before a response writes no row. A retry that throws and then receives one response writes one row. Closing a stream after a structured error does not write a second row. `complete_json` still does not write a row. The durable row has `prompt_tokens` and `completion_tokens` only. The in-memory counter still adds `prompt_tokens or 0`.

A grounded answer attaches the image on the `Role.CHAT` `complete_text` call inside the workflow. Calendar advice does not receive the image. The graph state does not hold the bytes.

`.env.example` says an unset `EMBEDDING_MODEL` means the code default `embedding-3`, and an unset `VISION_MODEL` means `glm-4.6v`. The example lines are overrides. An unset `LLM_MODEL_CONCURRENCY_LIMITS` is an empty map, and an unset `LLM_CONCURRENCY_DEFAULT` is 10. `config.py` defaults were not changed.

## Seam

`bind_images`, `load_image_blocks`, and `purge_pending` are the seam. This unit does not start a job loop, and it does not implement talk or resume. A restarted worker can rebuild the blocks only after a later unit calls the seam for a non-terminal job.

`bind_images` commits a `staged` row, under a unique `task-image/{user}/{task}/{uuid}` key, before `BlobStore.put`. It then marks the row `ready` only when the task is still non-terminal in that same update. A terminal task, or a row that disappeared, becomes `pending_delete` or is dropped, and an unreferenced key is deleted. Two images with the same bytes do not share a key, so deleting one does not delete the other. The caller's later rollback does not remove a bind that already committed.

A staged row on a still-running task is left alone until it is 10 minutes old. The beat then marks it `ready` if the blob exists, or deletes the row if the blob is missing. A staged row on a terminal task is queued on the next beat even when it is younger than 10 minutes. `stop` already marks a staged row that exists at the terminal commit.

This is not complete for one crash window. If the process is killed after `BlobStore.put` returns and after a concurrent purge has already removed the row, and before this process deletes that unreferenced key, the blob has no row. `BlobStore` has no list operation, so the beat cannot find that key. The live process deletes it when it continues. A bind whose put runs longer than 10 minutes can be reclaimed while the task is still running. `finish_reserved` then drops the unreferenced key and raises `ImageBindError` with code `expired`.

On Postgres, a usage row with a null `user_id` fails the row-level check. The insert is logged and the model answer still returns. Unbound callers can hit that path. The chat request binds the user and the generation before the model call.

## Model check

`python3 verification/picture/check.py` exits 0. TLC2 2026.11.06 from the vendored jar. 29 states generated, 16 distinct, depth 6. The rules that hold are `TypeOK`, `ImageStaysOnChatModel`, `MissingUsageStaysNull`, `PendingDeleteBeforeBlob`, `TerminalStays`, and `StrangerHidden`.

`--break-ocr` exits 1 and violates `ImageStaysOnChatModel`. `--break-zero` exits 1 and violates `MissingUsageStaysNull`. `--break-early-blob` exits 1 and violates `PendingDeleteBeforeBlob`. `--break-revert` exits 1 and violates `TerminalStays`. `--break-leak` exits 1 and violates `StrangerHidden`. A modeling failure exits 2. `.github/workflows/picture-tla.yml` runs the held check and requires the zero break to exit 1.

The model is one request. It does not include the provider image-safety review, a second user beyond the owner check, a restart during the HTTP call, a missing blob while the job is still running, the bind versus terminal interleaving, or talk and resume. Those races are pytest, not TLC. The missing-blob case is `test_missing_blob_is_unavailable_and_does_not_call_ocr`. `NoteFailed` is a provider call that never returns a response. A truncated response is still a counted or null usage row in the tests.

The statechart is `ev-picture-statechart.svg`. The held run draws 10 states and 12 arrows. The view hides the end stutter, 10 steps. No held state breaks a rule. The chart was drawn with Pele 1.9.0 from `verification/picture/Picture.tla` and `verification/picture/view.mjs`, with every break constant false. `TLA2TOOLS` pointed at `verification/tlc/tla2tools.jar`.

The checker anchors read the source. `chat.py` and `task_images.py` do not call `ocr_image_bytes`. `mark_pending_delete` is inside `_apply_event`. Blob purge is not. The in-memory counter still contains `prompt_tokens or 0`. The durable writer does not. Storage keys are checked for the `task-image/` prefix.

## Tests

From `backend`, with `DATABASE_URL=sqlite+pysqlite:///./ci-eval.db`, `REDIS_ENABLED=false`, `CELERY_TASK_ALWAYS_EAGER=true`, empty `SANDBOX_RUNNER_URL`, `SECRET_KEY=test-secret-key`, and `ADMIN_PASSWORD=test-admin-pass`. Python 3.12.3. The eval workflow runs `python -m pytest tests -q` on Python 3.11. That job is not the evidence here.

`python -m pytest tests/test_picture.py -o addopts= -q` exits 0. 25 passed. The picture file covers signature and size rejection, the compact body without `timeout`, the user image allowlist and the tool refusal, null usage and a real zero, a transport failure with no row, a truncated or filtered response that stores the returned counts, a response after stop, a stop before create with no row, one row across a retry, a stream error after usage with no second row on close, an oversized body that does not call the client, owner load and a failed blob delete that stays `pending_delete`, a missing blob that stays `ready` and does not call OCR, a committed bind that survives the caller rollback and is removed on stop, a terminal transition during put that leaves no ready image, staged cleanup by reconcile without another chat, distinct keys for the same bytes, an already-absent blob that drops the row, the chat PNG request, an empty-text image request through the route and workflow, the SSE error `body_limit` with no saved turn, the watchdog beat calling reconcile, regenerate rejection, the grounded chat call, and the SQLite upgrade from `0017_errand` through `0018_picture` and back. The upgraded `task_images` check includes `staged`.

`python -m pytest tests -o addopts= -q` exits 0. 918 passed, 1 xfailed, 7 skipped. Pytest 8.4.2. The earlier run of this branch was 910 passed. The added tests are the usage boundary, the bind race, the route checks, and the watchdog beat.

`tests/test_rls_postgres.py` adds force-RLS checks for `llm_calls` and `task_images`, hides Alice's rows from Bob, keeps a null prompt count, and expects a null `user_id` insert to fail. The file skips unless `DATABASE_URL` is Postgres. This machine has no Postgres. The rls job in `.github/workflows/eval.yml` runs `alembic upgrade head` and then that file. That job was not run here.

Frontend, from `frontend`. `npm test` exits 0. 40 passed. That includes the signature tests and the composer tests for a late read, remove, a rejected send, sent previews, and the 20MB server cap. `npm run build` is `tsc && vite build` and exits 0.

`verification/work-loop/check.py` exits 0 after the chat stream close.

## Flowchart

The product chart is `ev-picture-flowchart.svg`, drawn with Pele 1.9.0 from `ev-picture-flowchart.mmd`. The main path goes down. Refusals and the later job seam sit to the side. The blank node is a rank spacer. It is not a product step. The formal state diagram is `ev-picture-statechart.svg`. They are different pictures.

## Deferred

These were not run. They are not passed.

1. A live chat with a real PNG and a real provider.
2. An installed-package test that sends the body. The serializer match is a source reading of OpenAI 2.54.0 `openapi_dumps`.
3. Talk and resume calling `bind_images` and reading the blocks on a restarted worker.
4. The crash window after `BlobStore.put` and after the row is already gone, before the unreferenced delete. A bind that outlives the 10 minute staged hold is also not treated as a finished feature.
5. The stop and disconnect changes owned by later units. This unit still stops the task and skips finish when the generation is stopped.
6. The Postgres RLS job.
