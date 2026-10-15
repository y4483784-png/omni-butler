import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { isExpired, parseExpiresAt } from "../src/utils/scheduleExpiry.ts";

const SHANGHAI_OFFSET_MINUTES = -480;
const createdAt = Date.parse("2026-10-15T06:12:00.000Z");
const offsetLess = "2026-10-15T06:42:00";

test("offset-less expiry is UTC, so a new card is not already expired in UTC+8", () => {
  assert.equal(
    new Date().getTimezoneOffset(),
    SHANGHAI_OFFSET_MINUTES,
    "run with TZ=Asia/Shanghai so a local parse of the offset-less stamp is not UTC",
  );
  // Local parse of the container stamp is 06:42 UTC+8, about 7.5h before now.
  assert.ok(new Date(offsetLess).getTime() < createdAt);
  assert.equal(parseExpiresAt(offsetLess).toISOString(), "2026-10-15T06:42:00.000Z");
  assert.equal(isExpired({ phase: "draft", expires_at: offsetLess }, createdAt), false);
  assert.equal(isExpired({ phase: "draft", expires_at: "2026-10-15T06:42:00+00:00" }, createdAt), false);
  assert.equal(isExpired({ phase: "draft", expires_at: "2026-10-15T06:42:00.000Z" }, createdAt), false);
  assert.equal(
    isExpired({ phase: "draft", expires_at: offsetLess }, Date.parse("2026-10-15T06:42:00.000Z")),
    true,
  );
});

test("non-drafts and blank expiry stay open", () => {
  assert.equal(isExpired({ phase: "confirmed", expires_at: offsetLess }, createdAt), false);
  assert.equal(isExpired({ phase: "draft", expires_at: "" }, createdAt), false);
  assert.equal(isExpired({ phase: "draft" }, createdAt), false);
});

test("ScheduleCard writes expiresAt with toISOString", () => {
  const source = readFileSync(new URL("../src/components/ScheduleCard.tsx", import.meta.url), "utf8");
  assert.match(source, /new Date\(Date\.now\(\) \+ 30 \* 60 \* 1000\)\.toISOString\(\)/);
  assert.match(source, /import \{ isExpired \} from "\.\.\/utils\/scheduleExpiry"/);
  const expiresAt = new Date(createdAt + 30 * 60 * 1000).toISOString();
  assert.equal(expiresAt, "2026-10-15T06:42:00.000Z");
  assert.equal(isExpired({ phase: "draft", expires_at: expiresAt }, createdAt), false);
});
