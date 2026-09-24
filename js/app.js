// Cortaí — 100% no navegador. Nenhum vídeo sai do aparelho.
import {
  DETECT_W, DETECT_H, DETECT_FPS, MAX_DETECT_FRAMES, MAX_DURATION_S, EMPTY_MSG,
  FeatureAccumulator, detectFromFeatures, lpoName,
} from "./detector.js";

const $ = (id) => document.getElementById(id);
const form = $("form");
const fileInput = $("file");
const drop = $("drop");
const fileName = $("file-name");
const btnGo = $("btn-go");
const btnSample = $("btn-sample");
const progressWrap = $("progress-wrap");
const progressText = $("progress-text");
const progressFill = $("progress-fill");
const gallery = $("gallery");
const grid = $("grid");
const galleryMeta = $("gallery-meta");
const errBox = $("err");

const MAX_FILES = 8;
const MAX_EACH = 2 * 1024 * 1024 * 1024; // 2 GB (fica no aparelho)
const VENDOR = new URL("../vendor/", import.meta.url);
const objectUrls = [];

function showErr(msg) {
  errBox.hidden = !msg;
  errBox.textContent = msg || "";
}
function setProgress(text, frac) {
  progressWrap.hidden = false;
  if (text) progressText.textContent = text;
  if (typeof frac === "number")
    progressFill.style.width = `${Math.max(0, Math.min(1, frac)) * 100}%`;
}
function fmt(sec) {
  const s = Math.max(0, Number(sec) || 0);
  const m = Math.floor(s / 60);
  return `${m}:${(s % 60).toFixed(1).padStart(4, "0")}`;
}
function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;").replaceAll("'", "&#39;");
}
function safeDisplayName(name) {
  const base = String(name || "treino.mp4").split(/[\\/]/).pop().trim() || "treino.mp4";
  return base.replace(/[\u0000-\u001f\u007f-\u009f]/g, "").slice(0, 120) || "treino.mp4";
}

// ---------- seleção de arquivos ----------
function selectedFiles() {
  return Array.from(fileInput.files || []);
}
function refreshFileList() {
  const files = selectedFiles();
  btnGo.disabled = files.length === 0;
  fileName.hidden = !files.length;
  fileName.textContent = files
    .map((f) => `${f.name} · ${(f.size / (1024 * 1024)).toFixed(1)} MB`)
    .join("\n");
}
fileInput.addEventListener("change", refreshFileList);
["dragenter", "dragover"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drag"); })
);
drop.addEventListener("drop", (e) => {
  const files = e.dataTransfer && e.dataTransfer.files;
  if (files && files.length) {
    fileInput.files = files;
    refreshFileList();
  }
});

// ---------- 1) amostragem de frames: <video> + canvas, 10 fps, 180x320 ----------
function loadVideo(url) {
  return new Promise((resolve, reject) => {
    const v = document.createElement("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "auto";
    v.crossOrigin = "anonymous";
    let done = false;
    v.onloadeddata = () => {
      if (done) return;
      done = true;
      v.pause();
      resolve(v);
    };
    v.onerror = () => reject(new Error("Não consegui abrir esse vídeo neste navegador."));
    v.src = url;
    v.load();
    // iOS Safari só carrega depois de um play(); vídeo mudo pode tocar sem gesto.
    setTimeout(() => {
      if (!done) v.play().catch(() => {});
    }, 1500);
  });
}
function seekTo(v, t) {
  return new Promise((resolve, reject) => {
    const to = setTimeout(() => reject(new Error("seek timeout")), 15000);
    v.addEventListener("seeked", () => { clearTimeout(to); resolve(); }, { once: true });
    v.currentTime = t;
  });
}

async function sampleFeatures(file, onFrac) {
  const url = URL.createObjectURL(file);
  try {
    const v = await loadVideo(url);
    let duration = v.duration;
    if (!Number.isFinite(duration) || duration <= 0) throw new Error("Duração inválida no arquivo.");
    if (duration > MAX_DURATION_S)
      throw new Error(
        `${safeDisplayName(file.name)}: vídeo longo demais (${duration.toFixed(0)}s). Máximo ${MAX_DURATION_S}s por arquivo.`
      );
    const canvas = document.createElement("canvas");
    canvas.width = DETECT_W;
    canvas.height = DETECT_H;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const acc = new FeatureAccumulator();
    // ffmpeg fps=10:round=down → o frame k é o último frame da fonte antes de (k+1)/10 s
    const n = Math.min(MAX_DETECT_FRAMES, Math.max(1, Math.floor(duration * DETECT_FPS + 1e-6)));
    for (let k = 0; k < n; k++) {
      await seekTo(v, Math.min(duration - 0.001, (k + 1) / DETECT_FPS - 0.002));
      // estica pro 180x320 igual ao scale=180:320 do servidor (retrato já rotacionado pelo navegador)
      ctx.drawImage(v, 0, 0, DETECT_W, DETECT_H);
      acc.push(ctx.getImageData(0, 0, DETECT_W, DETECT_H).data, 4);
      if (k % 5 === 0) onFrac(k / n);
    }
    v.removeAttribute("src");
    v.load();
    return { features: acc.features(), duration, width: v.videoWidth, height: v.videoHeight };
  } finally {
    URL.revokeObjectURL(url);
  }
}

// ---------- 2) corte: ffmpeg.wasm (self-hosted, single-thread, sem COOP/COEP) ----------
let ffmpegPromise = null;
function getFFmpeg() {
  if (!ffmpegPromise) {
    ffmpegPromise = (async () => {
      const { FFmpeg } = await import("../vendor/ffmpeg/index.js");
      const ff = new FFmpeg();
      await ff.load({
        coreURL: new URL("core/ffmpeg-core.js", VENDOR).href,
        wasmURL: new URL("core/ffmpeg-core.wasm", VENDOR).href,
      });
      return ff;
    })().catch((e) => {
      ffmpegPromise = null;
      throw e;
    });
  }
  return ffmpegPromise;
}

async function cutClips(ff, file, windows, onClip) {
  const dir = `/in${Math.random().toString(36).slice(2, 8)}`;
  await ff.createDir(dir);
  let inPath;
  let mounted = false;
  try {
    await ff.mount("WORKERFS", { files: [file] }, dir);
    mounted = true;
    inPath = `${dir}/${file.name}`;
  } catch {
    inPath = `${dir}/input`;
    await ff.writeFile(inPath, new Uint8Array(await file.arrayBuffer()));
  }
  const out = [];
  try {
    for (let i = 0; i < windows.length; i++) {
      const w = windows[i];
      const dur = Math.max(0.05, w.end - w.start);
      const outName = `clip_${i}.mp4`;
      onClip(i, 0);
      const handler = ({ progress }) => onClip(i, progress);
      ff.on("progress", handler);
      const code = await ff.exec([
        "-ss", w.start.toFixed(3), "-i", inPath, "-t", dur.toFixed(3),
        "-map", "0:v:0", "-map", "0:a:0?",
        // letterbox 720x1280: mantém o atleta inteiro, sem crop (retrato já autorrotacionado)
        "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:black,setsar=1",
        "-fpsmax", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", outName,
      ]);
      ff.off("progress", handler);
      if (code !== 0) throw new Error("Falha ao cortar o clipe. Tenta outro trecho/arquivo.");
      const data = await ff.readFile(outName);
      await ff.deleteFile(outName);
      out.push(new Blob([data.buffer], { type: "video/mp4" }));
    }
  } finally {
    if (mounted) await ff.unmount(dir).catch(() => {});
    else await ff.deleteFile(inPath).catch(() => {});
    await ff.deleteDir(dir).catch(() => {});
  }
  return out;
}

// ---------- pipeline ----------
async function processFiles(files) {
  for (const u of objectUrls.splice(0)) URL.revokeObjectURL(u);
  const clips = [];
  const sources = [];
  const usedNames = new Set();
  const nf = files.length;
  const ffLoad = getFFmpeg(); // baixa o motor de corte em paralelo com a análise
  for (let fi = 0; fi < nf; fi++) {
    const file = files[fi];
    const name = safeDisplayName(file.name);
    const tag = nf > 1 ? ` (${fi + 1}/${nf})` : "";
    const base = fi / nf;
    setProgress(`Analisando movimento da barra${tag}…`, base);
    const { features, duration } = await sampleFeatures(file, (f) =>
      setProgress(null, base + (f * 0.5) / nf)
    );
    const windows = detectFromFeatures(features, duration);
    sources.push({ name, duration, lifts: windows.length });
    if (!windows.length) continue;
    setProgress(`Carregando o cortador${tag}…`, base + 0.5 / nf);
    const ff = await ffLoad;
    const blobs = await cutClips(ff, file, windows, (i, p) =>
      setProgress(
        `Cortando puxo ${i + 1}/${windows.length}${tag}…`,
        base + (0.5 + (0.5 * (i + Math.max(0, Math.min(1, p || 0)))) / windows.length) / nf
      )
    );
    windows.forEach((w, i) => {
      const n = clips.length + 1;
      let dl = lpoName(w.start);
      if (usedNames.has(dl)) dl = `cortai-lpo-${String(n).padStart(2, "0")}.mp4`;
      usedNames.add(dl);
      const url = URL.createObjectURL(blobs[i]);
      objectUrls.push(url);
      clips.push({
        n, source_name: name, start: w.start, end: w.end,
        duration: Math.round((w.end - w.start) * 1000) / 1000,
        reason: w.reason, download: dl, url, size: blobs[i].size,
      });
    });
  }
  setProgress("Pronto.", 1);
  const payload = { clips, sources };
  if (!clips.length) payload.message = EMPTY_MSG;
  return payload;
}

function render(data) {
  const clips = data.clips || [];
  const sources = data.sources || [];
  grid.innerHTML = "";
  gallery.hidden = clips.length === 0;
  const nsrc = sources.length;
  galleryMeta.textContent = clips.length
    ? `${clips.length} levantamento${clips.length > 1 ? "s" : ""}${nsrc > 1 ? ` · ${nsrc} vídeos` : ""}`
    : "";
  if (!clips.length) {
    showErr(data.message || EMPTY_MSG);
    return;
  }
  const groups = new Map();
  for (const c of clips) {
    if (!groups.has(c.source_name)) groups.set(c.source_name, []);
    groups.get(c.source_name).push(c);
  }
  const byName = new Map(sources.map((s) => [s.name, s]));
  for (const [name, group] of groups) {
    const head = document.createElement("div");
    head.className = "source-head";
    const src = byName.get(name);
    const extra = src
      ? `${src.lifts} puxo${src.lifts === 1 ? "" : "s"} · ${Number(src.duration).toFixed(0)}s`
      : `${group.length} corte${group.length > 1 ? "s" : ""}`;
    head.innerHTML = `<h3>${escapeHtml(name)}</h3><p>${escapeHtml(extra)}</p>`;
    grid.appendChild(head);
    for (const c of group) {
      const card = document.createElement("article");
      card.className = "clip";
      card.innerHTML = `
      <video src="${escapeHtml(c.url)}" controls playsinline preload="metadata"></video>
      <div class="clip-body">
        <div class="clip-meta">
          <span>${fmt(c.start)} – ${fmt(c.end)}</span>
          <span>${Number(c.duration).toFixed(1)}s</span>
        </div>
        <p class="reason">${escapeHtml(c.reason)}</p>
        <a class="btn dl" href="${escapeHtml(c.url)}" download="${escapeHtml(c.download)}">Baixar MP4</a>
      </div>`;
      grid.appendChild(card);
    }
  }
}

async function run(getFiles) {
  showErr("");
  gallery.hidden = true;
  btnGo.disabled = true;
  btnSample.disabled = true;
  setProgress("Preparando…", 0);
  try {
    const files = await getFiles();
    const data = await processFiles(files);
    window.__cortaiResult = data; // usado pelo teste e2e
    render(data);
  } catch (e) {
    console.error(e);
    window.__cortaiResult = { error: String((e && e.message) || e) };
    showErr((e && e.message) || "Falha ao processar.");
  } finally {
    progressWrap.hidden = true;
    btnSample.disabled = false;
    btnGo.disabled = selectedFiles().length === 0;
  }
}

form.addEventListener("submit", (e) => {
  e.preventDefault();
  const files = selectedFiles();
  if (!files.length) return;
  if (files.length > MAX_FILES) return showErr("No máximo 8 arquivos por vez.");
  for (const f of files) if (f.size > MAX_EACH) return showErr(`${f.name} é maior que 2 GB.`);
  run(async () => files);
});

btnSample.addEventListener("click", () =>
  run(async () => {
    const res = await fetch(new URL("../sample.mp4", import.meta.url));
    if (!res.ok) throw new Error("Não consegui baixar o treino de exemplo.");
    return [new File([await res.blob()], "treino-exemplo.mp4", { type: "video/mp4" })];
  })
);

$("btn-copy-pix").addEventListener("click", async () => {
  const key = $("pix-key").textContent.trim();
  try {
    await navigator.clipboard.writeText(key);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = key;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
  $("pix-copied").hidden = false;
  setTimeout(() => ($("pix-copied").hidden = true), 1800);
});
