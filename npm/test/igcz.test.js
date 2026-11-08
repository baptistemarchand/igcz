import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, mkdtempSync, readFileSync, readdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { gunzipSync } from "node:zlib";
import { IgczError, decode, encode, version } from "../index.js";

const root = new URL("../../", import.meta.url).pathname;

function synthetic() {
  const lines = ["AXXX001", "HFDTE010120", "HFPLTPILOTINCHARGE:Test Pilot"];
  for (let i = 0; i < 200; i++) {
    const t = 120000 + Math.floor(i / 60) * 100 + (i % 60);
    const lat = String(30000 + i * 3).padStart(5, "0");
    const alt = String(1000 + i).padStart(5, "0");
    lines.push(`B${t}47${lat}N00800${lat}EA${alt}${alt}`);
  }
  lines.push("LXXXtrailing note");
  return lines.join("\r\n") + "\r\n";
}

function corpusFlight() {
  const dir = process.env.IGC_CORPUS ?? join(root, "../open_igc_database/igc_gz");
  if (!existsSync(dir)) return null;
  const name = readdirSync(dir).find((f) => f.endsWith(".igc.gz"));
  return name ? gunzipSync(readFileSync(join(dir, name))) : null;
}

test("round trip is byte exact", async () => {
  const igc = Buffer.from(synthetic());
  const packed = await encode(igc);
  assert.ok(packed.length < igc.length / 2);
  assert.deepEqual(Buffer.from(await decode(packed)), igc);
});

test("accepts strings and ArrayBuffers", async () => {
  const text = synthetic();
  const packed = await encode(text);
  const bytes = await decode(packed.buffer.slice(packed.byteOffset, packed.byteOffset + packed.length));
  assert.equal(new TextDecoder().decode(bytes), text);
});

test("any text survives, including an empty file", async () => {
  for (const text of ["", "not an igc file", "B\n\n\r\nB1"]) {
    assert.equal(new TextDecoder().decode(await decode(await encode(text))), text);
  }
});

test("errors carry the Zig error name", async () => {
  await assert.rejects(decode(Buffer.from("hello")), (e) => e instanceof IgczError && e.code === "NotIgcz");
  const packed = await encode(synthetic());
  await assert.rejects(decode(packed.slice(0, packed.length - 20)), (e) => e instanceof IgczError && e.code === "Corrupt");
  await assert.rejects(encode(42), TypeError);
});

test("version matches the Zig library", async () => {
  const source = readFileSync(join(root, "src/igcz.zig"), "utf8");
  assert.equal(await version(), Number(source.match(/pub const version = (\d+);/)[1]));
});

const cli = join(root, "zig-out/bin/igcz");
test("files cross-decode with the Zig CLI", { skip: !existsSync(cli) && "zig-out/bin/igcz not built" }, async () => {
  const dir = mkdtempSync(join(tmpdir(), "igcz-npm-"));
  for (const igc of [Buffer.from(synthetic()), corpusFlight()].filter(Boolean)) {
    writeFileSync(join(dir, "in.igc"), igc);
    // npm encodes, Zig decodes.
    writeFileSync(join(dir, "npm.igcz"), await encode(igc));
    assert.equal(spawnSync(cli, ["decode", join(dir, "npm.igcz"), join(dir, "out.igc")]).status, 0);
    assert.deepEqual(readFileSync(join(dir, "out.igc")), igc);
    // Zig encodes, npm decodes.
    assert.equal(spawnSync(cli, ["encode", join(dir, "in.igc"), join(dir, "zig.igcz")]).status, 0);
    assert.deepEqual(Buffer.from(await decode(readFileSync(join(dir, "zig.igcz")))), igc);
  }
});
