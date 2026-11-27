"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const queue = require("../apps/web/assets/upload-batch.js");
const file = name => ({name, size: 17});

test("every valid original is sent serially in selected order", async () => {
  const items = queue.create([file("one.txt"), file("two.PPTX"), file("three.md")]);
  const called = []; let inFlight = 0;
  const summary = await queue.run(items, async item => {
    assert.equal(++inFlight, 1); called.push(item.file.name);
    await Promise.resolve(); inFlight--;
    return {id: "original-" + item.index};
  });
  assert.deepEqual(called, ["one.txt", "two.PPTX", "three.md"]);
  assert.equal(summary.SAVED, 3);
});
test("rejection does not discard neighbours or reupload already saved originals", async () => {
  const items = queue.create([file("one.txt"), file("invalid.png"), file("three.md")]);
  const calls = [];
  await queue.run(items, async item => {
    calls.push(item.index);
    if (item.index === 1) throw Object.assign(new Error("real rejection"), {status: 422});
    return {id: String(item.index)};
  });
  const saved = items[0].saved;
  await queue.run(items, async item => {calls.push(item.index); return {id: "corrected"};});
  assert.deepEqual(calls, [0, 1, 2, 1]);
  assert.strictEqual(items[0].saved, saved);
});
test("ambiguous transport outcome stops the tail and is never automatically retried", async () => {
  const items = queue.create([file("one.txt"), file("two.txt")]);
  const calls = [];
  await queue.run(items, async item => {calls.push(item.index); throw new Error("connection lost");});
  assert.equal(items[0].state, "UNKNOWN"); assert.equal(items[1].state, "PENDING");
  await queue.run(items, async item => {calls.push(item.index); return {id: "second"};});
  assert.deepEqual(calls, [0, 1]);
  assert.equal(items[0].state, "UNKNOWN");
});
test("500 and malformed success do not become safe rejection retries", async () => {
  for (const send of [async () => {throw Object.assign(new Error("500"), {status: 500});}, async () => ({})]) {
    const items = queue.create([file("one.txt")]);
    await queue.run(items, send); assert.equal(items[0].state, "UNKNOWN");
  }
});
test("stale form keeps the original accepted receipt and never sends the second file", async () => {
  const items = queue.create([file("one.txt"), file("two.txt")]); let current = true;
  const calls = [], notifications = [];
  await queue.run(items, async item => {
    calls.push(item.index); current = false; return {id: "persisted-before-switch"};
  }, item => notifications.push(item.state), () => current);
  assert.deepEqual(calls, [0]); assert.deepEqual(notifications, ["UPLOADING"]);
  assert.equal(items[0].saved.id, "persisted-before-switch");
  assert.equal(items[1].state, "PENDING");
});
test("unsupported and oversized originals are reported individually, never silently filtered", async () => {
  const items = queue.create([file("legacy.ppt"), {name: "large.txt", size: 1048577}, file("valid.docx")]);
  const calls = []; await queue.run(items, async item => {calls.push(item.index); return {id: "last"};});
  assert.equal(items.length, 3); assert.deepEqual(calls, [2]);
  assert.equal(queue.counts(items).INVALID, 2); assert.equal(queue.counts(items).SAVED, 1);
});
test("the original 1MiB and 16MiB per-file limits remain exact", () => {
  assert.equal(queue.errorFor({name: "one.md", size: 1048576}), null);
  assert.match(queue.errorFor({name: "one.md", size: 1048577}), /1 MiB/);
  assert.equal(queue.errorFor({name: "one.pptx", size: 16777216}), null);
  assert.match(queue.errorFor({name: "one.pptx", size: 16777217}), /16 MiB/);
});
