import assert from "node:assert/strict";
import test from "node:test";

import { imageFromFile, sniffImage } from "../src/chatImage.ts";

test("sniff accepts the four chat signatures and rejects a mismatch", () => {
  assert.equal(sniffImage(Uint8Array.from([0xff, 0xd8, 0x00])), "image/jpeg");
  assert.equal(sniffImage(Uint8Array.from([0x89, 0x50, 0x4e, 0x47, 0x0d])), "image/png");
  assert.equal(sniffImage(Uint8Array.from([0x47, 0x49, 0x46, 0x38, 0x39, 0x61])), "image/gif");
  const webp = new Uint8Array(12);
  webp.set([0x52, 0x49, 0x46, 0x46], 0);
  webp.set([0x57, 0x45, 0x42, 0x50], 8);
  assert.equal(sniffImage(webp), "image/webp");
  assert.equal(sniffImage(Uint8Array.from([0x00, 0x01, 0x02, 0x03])), null);
});

test("a declared type that disagrees with the bytes is dropped", () => {
  const png = Uint8Array.from([0x89, 0x50, 0x4e, 0x47]);
  const file = new File([png], "x.png", { type: "image/jpeg" });
  assert.equal(imageFromFile(file, png), null);
  const plain = new File([png], "x.png", { type: "" });
  const accepted = imageFromFile(plain, png);
  assert.equal(accepted && accepted.media_type, "image/png");
  assert.equal(typeof accepted.data_base64, "string");
  assert.ok(accepted.data_base64.length > 0);
});
