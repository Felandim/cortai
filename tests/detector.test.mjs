// Node: roda o detector JS nos mesmos frames que o Python usa (ffmpeg fps=10, 180x320)
// e compara com tests/fixtures/expected.json (gerado por tests/make_fixtures.py).
//   node tests/detector.test.mjs
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  DETECT_W, DETECT_H, DETECT_FPS, MAX_DETECT_FRAMES, MAX_DURATION_S,
  FeatureAccumulator, detectFromFeatures, pyRound, smooth, fitClip,
} from "../js/detector.js";

const HERE = dirname(fileURLToPath(import.meta.url));
const FIX = join(HERE, "fixtures");
const TOL = Number(process.env.CORTAI_TOL || 0.35);
let failed = 0;
const check = (ok, msg) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${msg}`);
  if (!ok) failed++;
};

// unit checks
check(pyRound(2.5) === 2 && pyRound(3.5) === 4 && pyRound(70.4) === 70, "pyRound = round() do Python");
const sm = smooth(Float64Array.from([0, 0, 10, 0, 0]), 2);
check(Math.abs(sm[1] - 0) < 1e-9 && Math.abs(sm[2] - 5) < 1e-9, "smooth janela par = np.convolve");
// valores de referência de processor._fit_clip
check(JSON.stringify([fitClip(10, 12, 40, 11), fitClip(1, 3, 40, 2), fitClip(20, 31, 40, 25)])
  === "[[6.5,13.5],[0,7],[20.5,27.5]]", "fitClip = _fit_clip do Python");

function frames(path, duration) {
  const cap = Math.min(duration, MAX_DURATION_S).toFixed(3);
  const r = spawnSync("ffmpeg", [
    "-v", "error", "-t", cap, "-i", path, "-an",
    "-vf", `fps=${DETECT_FPS}:round=down,scale=${DETECT_W}:${DETECT_H},format=rgb24`,
    "-frames:v", String(MAX_DETECT_FRAMES), "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
  ], { maxBuffer: 1 << 30 });
  if (r.status !== 0) throw new Error(String(r.stderr));
  return r.stdout;
}

const expected = JSON.parse(readFileSync(join(FIX, "expected.json"), "utf8"));
for (const [name, exp] of Object.entries(expected)) {
  const raw = frames(join(FIX, name), exp.duration);
  const fs = DETECT_W * DETECT_H * 3;
  const acc = new FeatureAccumulator();
  for (let i = 0; i + fs <= raw.length; i += fs) acc.push(raw.subarray(i, i + fs), 3);
  const got = detectFromFeatures(acc.features(), exp.duration);
  const e = exp.windows;
  let ok = got.length === e.length;
  for (let k = 0; ok && k < e.length; k++) {
    ok = Math.abs(got[k].start - e[k].start) <= TOL && Math.abs(got[k].end - e[k].end) <= TOL
      && got[k].reason === e[k].reason;
  }
  check(ok, `${name}: JS ${JSON.stringify(got.map((w) => [w.start, w.end]))} vs Python ${JSON.stringify(e.map((w) => [w.start, w.end]))}`);
}
if (failed) {
  console.log(`${failed} falha(s)`);
  process.exit(1);
}
console.log("OK");
