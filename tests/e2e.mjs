// E2E headless: abre o site, clica em "Testar com treino de exemplo" (e sobe os fixtures),
// espera os clipes MP4 e compara janelas com o Python.
//   CORTAI_URL=http://127.0.0.1:8099/ node tests/e2e.mjs
import { chromium } from "playwright";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const URL_ = process.env.CORTAI_URL || "http://127.0.0.1:8099/";
const TOL = Number(process.env.CORTAI_TOL || 0.35);
const expected = JSON.parse(readFileSync(join(HERE, "fixtures", "expected.json"), "utf8"));
const browser = await chromium.launch({ channel: process.env.CORTAI_CHANNEL || undefined });
const page = await browser.newPage();
const net = [];
page.on("response", (r) => net.push(`${r.status()} ${r.url()}`));
page.on("console", (m) => m.type() === "error" && console.log("console:", m.text()));
await page.goto(URL_, { waitUntil: "load" });
let failed = 0;

async function runCase(label, action, expWindows) {
  await page.evaluate(() => (window.__cortaiResult = undefined));
  const t0 = Date.now();
  await action();
  await page.waitForFunction(() => window.__cortaiResult !== undefined, null, { timeout: 600000 });
  const res = await page.evaluate(async () => {
    const r = window.__cortaiResult;
    if (r.error) return r;
    const out = [];
    for (const c of r.clips) {
      const b = await (await fetch(c.url)).arrayBuffer();
      const head = new TextDecoder().decode(new Uint8Array(b.slice(4, 8)));
      const v = document.createElement("video");
      v.src = c.url;
      await new Promise((ok) => { v.onloadedmetadata = ok; v.onerror = ok; });
      out.push({ start: c.start, end: c.end, reason: c.reason, download: c.download,
        bytes: b.byteLength, ftyp: head, w: v.videoWidth, h: v.videoHeight, dur: v.duration });
    }
    return { clips: out, sources: r.sources };
  });
  const secs = ((Date.now() - t0) / 1000).toFixed(1);
  if (res.error) {
    console.log(`FAIL ${label}: ${res.error}`);
    failed++;
    return;
  }
  let ok = res.clips.length === expWindows.length && res.clips.length > 0;
  for (let k = 0; ok && k < expWindows.length; k++) {
    const c = res.clips[k], e = expWindows[k];
    ok = Math.abs(c.start - e.start) <= TOL && Math.abs(c.end - e.end) <= TOL && c.ftyp === "ftyp"
      && c.bytes > 1000 && (c.w === 0 || (c.w === 720 && c.h === 1280));
  }
  console.log(`${ok ? "ok  " : "FAIL"} ${label} (${secs}s): ${JSON.stringify(res.clips.map((c) => [c.start, c.end, c.w + "x" + c.h, c.bytes, c.download]))} vs Python ${JSON.stringify(expWindows.map((e) => [e.start, e.end]))}`);
  if (!ok) failed++;
}

await runCase("botão treino de exemplo", () => page.click("#btn-sample"), expected["sample.mp4"].windows);
for (const name of ["yellow_portrait.mp4", "yellow_rotated.mp4"]) {
  await runCase(`upload ${name}`, async () => {
    await page.setInputFiles("#file", join(HERE, "fixtures", name));
    await page.click("#btn-go");
  }, expected[name].windows);
}
console.log("rede:", net.filter((l) => /vendor|sample|js\/|css\//.test(l)).join("\n      "));
await browser.close();
if (failed) process.exit(1);
console.log("E2E OK");
