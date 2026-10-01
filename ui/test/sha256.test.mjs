// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
// AC-07: the panel's SHA-256 and HMAC agree with the standards and with fbd's proof.
import assert from "node:assert/strict";
import { createHash, createHmac } from "node:crypto";
import { test } from "node:test";
import { load } from "./bundle.mjs";

const { sha256, hmacSha256, hex, proof } = await load("src/sha256.ts");
const enc = (s) => new TextEncoder().encode(s);

test("SHA-256 of standard and odd lengths", () => {
  assert.equal(hex(sha256(enc("abc"))), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
  for (const n of [0, 1, 55, 56, 63, 64, 65, 119, 1000]) {
    const s = "é".repeat(n);
    assert.equal(hex(sha256(enc(s))), createHash("sha256").update(s).digest("hex"), `length ${n}`);
  }
});

test("HMAC-SHA256: RFC 4231 cases 2 and 6", () => {
  assert.equal(hex(hmacSha256(enc("Jefe"), enc("what do ya want for nothing?"))),
    "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843");
  assert.equal(hex(hmacSha256(new Uint8Array(131).fill(0xaa), enc("Test Using Larger Than Block-Size Key - Hash Key First"))),
    "60e431591ee0b67f0d8a26aacbf5b77f8e0bc6213728c5140546040f0ee37f54");
});

test("the proof is fbd's (the same vector as fbd/src/local.rs)", () => {
  assert.equal(proof("0123456789abcdef0123456789abcdef", "00112233445566778899aabbccddeeff"),
    "30910a59a018ef3078942956ada86eb396a5f08e543e9612ec01ea3568b1a7e0");
  assert.equal(proof("x".repeat(32), "ab".repeat(16)),
    createHmac("sha256", "x".repeat(32)).update("fbd-hello-v1:" + "ab".repeat(16)).digest("hex"));
});
