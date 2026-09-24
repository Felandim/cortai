"""Cortaí — detecta levantamentos de LPO (rims amarelas / movimento vertical) e corta 9:16."""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import numpy as np

FFMPEG = "/usr/bin/ffmpeg"
FFPROBE = "/usr/bin/ffprobe"


class ProcessError(Exception):
    pass


def _safe_display_name(name: str, fallback: str = "treino.mp4") -> str:
    base = Path(name or fallback).name.strip() or fallback
    base = "".join(ch for ch in base if 32 <= ord(ch) < 127 or ord(ch) > 159)
    return (base[:120] or fallback)

JOBS_ROOT = Path("/tmp/cortai-jobs")
JOB_ID_RE = __import__("re").compile(r"^[a-f0-9]{8,32}$")
JOB_TTL_S = 6 * 3600


def safe_job_id(job_id: str) -> str:
    jid = (job_id or "").strip().lower()
    if not JOB_ID_RE.fullmatch(jid):
        raise ProcessError("Job inválido.")
    return jid


def job_dir_for(job_id: str) -> Path:
    jid = safe_job_id(job_id)
    root = JOBS_ROOT.resolve()
    path = (JOBS_ROOT / jid).resolve()
    if path != root / jid:
        raise ProcessError("Job inválido.")
    return path


def cleanup_old_jobs(ttl_s: int = JOB_TTL_S) -> int:
    """Delete job dirs older than ttl. Best-effort."""
    import time, shutil
    root = JOBS_ROOT
    if not root.exists():
        return 0
    now = time.time()
    removed = 0
    for child in root.iterdir():
        if not child.is_dir():
            continue
        try:
            age = now - child.stat().st_mtime
            if age > ttl_s:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            pass
    return removed

SAMPLE_PATH = Path(__file__).resolve().parent / "sample.mp4"

# Detector frames: autorotate (ffmpeg default) so iPhone MOVs yield portrait pixels.
DETECT_FPS = 10.0
MAX_DURATION_S = 240.0
MAX_DETECT_FRAMES = 2400  # 240s @ 10fps
DETECT_W = 180
DETECT_H = 320
YELLOW_MIN_PX = 25
YELLOW_TOP_FRAC = 0.22
YELLOW_TOP_REJECT = 0.50
SMOOTH_S = 0.4
MIN_TRAVEL_FRAC = 0.18
LIFT_MIN_S = 0.6
LIFT_MAX_S = 3.5
BURST_MAX_S = 3.0
MIN_ATTEMPT_GAP_S = 6.0
MIN_CLIP = 5.0
MAX_CLIP = 9.0
PREF_CLIP = 7.0
MAX_CLIPS_PER_FILE = 8
EOF_IGNORE_S = 4.0
START_TAIL_FRAC = 0.12
START_TAIL_MIN_S = 3.0
CENTER_TOP_FRAC = 0.22
CENTER_BOT_FRAC = 0.70
REL_PEAK_FRAC = 0.58
CUT_TIMEOUT_S = 180
EMPTY_MSG = (
    "Não achei levantamento nesse arquivo — tenta um take mais próximo da plataforma."
)




def _run(cmd: list[str], timeout: int = 90) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )


def _run_bin(cmd: list[str], timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )


def probe_duration(path: Path) -> float:
    r = _run(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        timeout=20,
    )
    if r.returncode != 0:
        raise ProcessError("Não deu pra ler a duração do vídeo (ffprobe).")
    try:
        return float(r.stdout.strip())
    except ValueError as e:
        raise ProcessError("Duração inválida no arquivo.") from e


def probe_is_video(path: Path) -> bool:
    r = _run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        timeout=20,
    )
    return r.returncode == 0 and "video" in (r.stdout or "").lower()


def probe_has_audio(path: Path) -> bool:
    r = _run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(path),
        ],
        timeout=20,
    )
    return r.returncode == 0 and "audio" in (r.stdout or "").lower()


def _bar_y(t: float, lifts: tuple[float, ...], y_low: int, y_high: int, up: float, hold: float, drop: float) -> int:
    for t0 in lifts:
        if t0 <= t < t0 + up:
            u = (t - t0) / up
            return int(round(y_low + (y_high - y_low) * u))
        if t0 + up <= t < t0 + up + hold:
            return y_high
        if t0 + up + hold <= t < t0 + up + hold + drop:
            u = (t - t0 - up - hold) / drop
            return int(round(y_high + (y_low - y_high) * u))
    return y_low


def generate_sample(out: Path = SAMPLE_PATH) -> Path:
    """~40s 1280x720, 3 levantamentos sintéticos (barra sobe, segura, cai + thud)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    w, h, fps, dur_s = 1280, 720, 24, 40
    nframes = fps * dur_s
    lifts = (5.0, 18.0, 31.0)
    up, hold, drop = 1.2, 1.0, 0.28
    y_low, y_high = 600, 160
    thuds_ms = [int(round((t + up + hold + drop) * 1000)) for t in lifts]
    fc = (
        f"[1:a]volume=0.04[bg];"
        f"[2:a]adelay={thuds_ms[0]}|{thuds_ms[0]}[t1];"
        f"[3:a]adelay={thuds_ms[1]}|{thuds_ms[1]}[t2];"
        f"[4:a]adelay={thuds_ms[2]}|{thuds_ms[2]}[t3];"
        f"[bg][t1][t2][t3]amix=inputs=4:duration=first:dropout_transition=0:normalize=0[a]"
    )
    cmd = [
        FFMPEG, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}", "-r", str(fps),
        "-i", "pipe:0",
        "-f", "lavfi", "-i", "anoisesrc=color=white:amplitude=0.025:duration=40:sample_rate=44100",
        "-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=1:duration=0.09:sample_rate=44100",
        "-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=1:duration=0.09:sample_rate=44100",
        "-f", "lavfi", "-i", "anoisesrc=color=brown:amplitude=1:duration=0.09:sample_rate=44100",
        "-filter_complex", fc,
        "-map", "0:v", "-map", "[a]",
        "-t", "40",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "48k", "-ac", "1",
        "-movflags", "+faststart",
        str(out),
    ]
    log_path = out.with_suffix(".ffmpeg.log")
    logf = log_path.open("wb")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=logf, stderr=logf
    )
    assert proc.stdin is not None
    base = np.zeros((h, w, 3), dtype=np.uint8)
    base[:, :] = (20, 18, 16)
    base[680:, :] = (44, 42, 38)
    try:
        for i in range(nframes):
            t = i / float(fps)
            fr = base.copy()
            y = max(24, min(h - 50, _bar_y(t, lifts, y_low, y_high, up, hold, drop)))
            fr[y : y + 22, 390:890] = (244, 241, 234)
            py = max(0, y - 10)
            fr[py : py + 42, 366:402] = (224, 220, 210)
            fr[py : py + 42, 878:914] = (224, 220, 210)
            proc.stdin.write(fr.tobytes())
        proc.stdin.close()
        proc.wait(timeout=90)
    except Exception:
        proc.kill()
        logf.close()
        raise
    logf.close()
    err = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    log_path.unlink(missing_ok=True)
    if proc.returncode != 0 or not out.exists() or out.stat().st_size < 1000:
        raise ProcessError("Falha ao gerar o vídeo de exemplo.")
    return out


def _smooth(x: np.ndarray, win: int) -> np.ndarray:
    if x.size == 0:
        return x
    w = max(1, int(win))
    if w == 1 or x.size == 1:
        return x.astype(np.float32, copy=False)
    kernel = np.ones(w, dtype=np.float32) / float(w)
    pad = w // 2
    padded = np.pad(x.astype(np.float32), (pad, pad), mode="edge")
    out = np.convolve(padded, kernel, mode="valid")
    return out[: x.size].astype(np.float32)


def _interp_nan(x: np.ndarray) -> np.ndarray:
    y = np.asarray(x, dtype=np.float32).copy()
    n = int(y.size)
    if n == 0:
        return y
    good = np.isfinite(y)
    if int(good.sum()) == 0:
        return y
    if int(good.sum()) == 1:
        y[:] = y[good][0]
        return y
    idx = np.arange(n, dtype=np.float32)
    y[~good] = np.interp(idx[~good], idx[good], y[good])
    return y


def _read_detect_frames(path: Path, duration: float | None = None) -> np.ndarray:
    """RGB frames WITH autorotate, fps=10, 180x320 portrait-ish pixels. Duration-capped."""
    cap_t = MAX_DURATION_S
    if duration is not None and duration > 0:
        cap_t = min(float(duration), MAX_DURATION_S)
    cmd = [
        FFMPEG,
        "-v",
        "error",
        "-t",
        f"{cap_t:.3f}",
        "-i",
        str(path),
        "-an",
        "-vf",
        f"fps={DETECT_FPS}:round=down,scale={DETECT_W}:{DETECT_H},format=rgb24",
        "-frames:v",
        str(MAX_DETECT_FRAMES),
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    r = _run_bin(cmd, timeout=CUT_TIMEOUT_S)
    raw = r.stdout or b""
    frame_size = DETECT_W * DETECT_H * 3
    n = len(raw) // frame_size if frame_size else 0
    if n < 1:
        return np.zeros((0, DETECT_H, DETECT_W, 3), dtype=np.uint8)
    if n > MAX_DETECT_FRAMES:
        n = MAX_DETECT_FRAMES
    return np.frombuffer(raw, dtype=np.uint8, count=n * frame_size).reshape(
        n, DETECT_H, DETECT_W, 3
    )


def _yellow_mask(fr: np.ndarray) -> np.ndarray:
    """HSV-ish yellow: bumper rims (bright yellow outer ring)."""
    r = fr[:, :, 0].astype(np.float32)
    g = fr[:, :, 1].astype(np.float32)
    b = fr[:, :, 2].astype(np.float32)
    return (r > g * 1.1) & (g > b * 1.15) & (r > 80.0) & ((r + g) > 1.4 * (b + 10.0))


def _yellow_cy(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray, bool]:
    """Centroid y of yellow pixels, ignoring the top 22% (ceiling lights)."""
    n = int(frames.shape[0])
    h = int(frames.shape[1])
    top_cut = int(round(YELLOW_TOP_FRAC * h))
    cy = np.full(n, np.nan, dtype=np.float32)
    counts = np.zeros(n, dtype=np.int32)
    counts_all = np.zeros(n, dtype=np.int32)
    counts_top = np.zeros(n, dtype=np.int32)
    ys_idx = np.arange(h, dtype=np.float32)[:, None]
    for i in range(n):
        m = _yellow_mask(frames[i])
        c_all = int(m.sum())
        c_top = int(m[:top_cut].sum()) if top_cut > 0 else 0
        counts_all[i] = c_all
        counts_top[i] = c_top
        if top_cut > 0:
            m = m.copy()
            m[:top_cut] = False
        c = int(m.sum())
        counts[i] = c
        if c >= YELLOW_MIN_PX:
            cy[i] = float((m.astype(np.float32) * ys_idx).sum() / c)
    tot = int(counts_all.sum())
    tot_top = int(counts_top.sum())
    lights = tot > 0 and (tot_top / float(tot)) >= YELLOW_TOP_REJECT
    ok = (not lights) and bool(
        (counts >= YELLOW_MIN_PX).mean() > 0.30
        and float(np.median(counts)) >= YELLOW_MIN_PX
    )
    return cy, counts, ok


def _center_column_mad(frames: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Luma MAD in the center column (middle 50% width, skip top 22% lights).

    Bottom 30% is returned separately so walking-into-camera can be rejected.
    """
    n = int(frames.shape[0])
    h = int(frames.shape[1])
    w = int(frames.shape[2])
    z = np.zeros(max(n, 1), dtype=np.float32)
    if n < 2:
        return z, z.copy()
    luma = (
        0.30 * frames[:, :, :, 0].astype(np.float32)
        + 0.59 * frames[:, :, :, 1].astype(np.float32)
        + 0.11 * frames[:, :, :, 2].astype(np.float32)
    )
    mad = np.abs(luma[1:] - luma[:-1])
    x0, x1 = w // 4, 3 * w // 4
    y0 = int(round(CENTER_TOP_FRAC * h))
    y1 = int(round(CENTER_BOT_FRAC * h))
    yb = int(round(0.70 * h))
    y1 = max(y0 + 1, min(h, y1))
    cen = mad[:, y0:y1, x0:x1].mean(axis=(1, 2))
    bot = mad[:, yb:, :].mean(axis=(1, 2))

    def _pad(src: np.ndarray) -> np.ndarray:
        out = np.empty(n, dtype=np.float32)
        out[0] = float(src[0]) if src.size else 0.0
        out[1:] = src
        return out

    return _pad(cen), _pad(bot)


def _tail_guard_s(duration: float) -> float:
    return max(START_TAIL_FRAC * float(duration), START_TAIL_MIN_S)


def _search_bounds(n: int, duration: float) -> tuple[int, int]:
    """Peak search skips the last 4.0s of every take (camera-off / walk-in)."""
    skip0 = int(round(1.5 * DETECT_FPS))
    skip1 = n - int(round(EOF_IGNORE_S * DETECT_FPS))
    t_lim = int(round(max(0.0, float(duration) - EOF_IGNORE_S) * DETECT_FPS))
    skip1 = min(skip1, t_lim)
    if skip1 <= skip0 + 4:
        skip0 = int(round(0.4 * DETECT_FPS))
        skip1 = max(skip0 + 4, n - int(round(0.4 * DETECT_FPS)))
    skip0 = max(1, skip0)
    skip1 = min(n - 1, max(skip0 + 3, skip1))
    return skip0, skip1


def _keep_window(start: float, end: float, duration: float, anchor: float) -> bool:
    if start >= float(duration) - _tail_guard_s(duration):
        return False
    if float(anchor) >= float(duration) - EOF_IGNORE_S:
        return False
    # Last 5–9s of a gym take is bar-down + walk to the phone — never a lift.
    if (
        float(duration) >= 14.0
        and end >= float(duration) - 0.25
        and start >= float(duration) - (MAX_CLIP + 0.6)
    ):
        return False
    d = end - start
    if float(duration) >= MIN_CLIP and d < MIN_CLIP - 0.05:
        return False
    if d > MAX_CLIP + 0.05:
        return False
    return True


def _fit_clip(
    start: float, end: float, duration: float, anchor: float
) -> tuple[float, float] | None:
    """Clamp 5–9s, prefer ~7s, more pre-roll than tail. Never grow a near-EOF peak."""
    duration = float(duration)
    anchor = float(anchor)
    start = max(0.0, float(start))
    end = min(duration, max(start + 0.05, float(end)))
    near_eof = anchor >= duration - EOF_IGNORE_S
    d = end - start
    if d < PREF_CLIP:
        need = PREF_CLIP - d
        add_l = min(need * 0.70, start)
        start -= add_l
        need -= add_l
        if not near_eof:
            add_r = min(need, max(0.0, duration - end))
            end += add_r
            need -= add_r
        if need > 0:
            start = max(0.0, start - need)
            if not near_eof:
                end = min(duration, start + PREF_CLIP)
    d = end - start
    if d < MIN_CLIP:
        need = MIN_CLIP - d
        add_l = min(need, start)
        start -= add_l
        need -= add_l
        if need > 0 and not near_eof:
            end = min(duration, end + need)
        start = max(0.0, start)
        if end - start < MIN_CLIP - 0.05:
            return None
    d = end - start
    if d > MAX_CLIP:
        left = min(4.5, PREF_CLIP * 0.65)
        start = max(0.0, anchor - left)
        end = min(duration, start + PREF_CLIP)
        if near_eof:
            end = min(end, max(anchor + 2.5, start + MIN_CLIP))
            if end - start > MAX_CLIP:
                end = start + MAX_CLIP
            if end - start < MIN_CLIP - 0.05:
                return None
    start = max(0.0, start)
    end = min(duration, max(start + 0.05, end))
    if not _keep_window(start, end, duration, anchor):
        return None
    return round(start, 3), round(end, 3)


def _pick_spaced(indices: list[int], scores: list[float], min_dist: int) -> list[int]:
    order = sorted(range(len(indices)), key=lambda k: scores[k], reverse=True)
    picked: list[int] = []
    for k in order:
        i = indices[k]
        if all(abs(i - j) >= min_dist for j in picked):
            picked.append(i)
    picked.sort()
    return picked


def _is_camera_approach(
    i: int, cen_s: np.ndarray, bot_s: np.ndarray, n: int, duration: float
) -> bool:
    """Walk-into-floor-cam: huge bottom-30% MAD and activity ramping toward EOF."""
    t = i / DETECT_FPS
    if t < 0.62 * float(duration):
        return False
    bot_i = float(bot_s[i])
    cen_i = float(cen_s[i])
    tail0 = int(round(0.82 * n))
    mid0, mid1 = int(round(0.15 * n)), int(round(0.65 * n))
    bot_tail = float(np.median(bot_s[tail0:])) if tail0 < n else 0.0
    bot_mid = float(np.median(bot_s[mid0:mid1])) if mid1 > mid0 else 0.0
    growing = bot_tail > bot_mid * 1.35 + 0.8
    huge_bot = bot_i >= 5.0 and bot_i >= 1.15 * max(cen_i, 1e-6)
    return bool(growing and huge_bot)


def _windows_from_yellow(
    cy_s: np.ndarray, up_vel: np.ndarray, duration: float
) -> list[tuple[float, float, str]]:
    """Lift = cy drops ≥18% of H within 0.6–3.5s. Last 4s of the file is ignored."""
    n = int(cy_s.size)
    if n < 8:
        return []
    h = float(DETECT_H)
    min_drop = MIN_TRAVEL_FRAC * h
    i_min = int(round(LIFT_MIN_S * DETECT_FPS))
    i_max = int(round(LIFT_MAX_S * DETECT_FPS))
    skip0, skip1 = _search_bounds(n, duration)
    cands: list[tuple[float, int, int]] = []
    for i0 in range(max(0, skip0), max(0, skip1)):
        i1 = min(skip1 - 1 if skip1 < n else n - 1, i0 + i_max)
        if i1 - i0 < i_min:
            continue
        wdw = cy_s[i0 : i1 + 1]
        kmin = int(np.argmin(wdw))
        if kmin < i_min:
            continue
        drop = float(cy_s[i0] - wdw[kmin])
        if drop < min_drop:
            continue
        stretch = up_vel[i0 : i0 + kmin + 1]
        peak_i = i0 + int(np.argmax(stretch))
        if peak_i >= skip1:
            continue
        cands.append((drop, i0, peak_i))
    if not cands:
        return []
    min_dist = int(round(MIN_ATTEMPT_GAP_S * DETECT_FPS))
    cands.sort(key=lambda t: t[0], reverse=True)
    picked: list[tuple[float, int, int]] = []
    for drop, i0, peak_i in cands:
        if any(abs(peak_i - p) < min_dist for _, _, p in picked):
            continue
        picked.append((drop, i0, peak_i))
    picked.sort(key=lambda t: t[2])
    windows: list[tuple[float, float, str]] = []
    for drop, i0, peak_i in picked:
        lo = max(0, peak_i - int(round(1.0 * DETECT_FPS)))
        hi = min(n, peak_i + int(round(0.5 * DETECT_FPS)) + 1)
        peak_v = float(np.max(up_vel[lo:hi])) if hi > lo else float(up_vel[peak_i])
        thresh_v = 0.30 * max(peak_v, 1e-6)
        i = int(peak_i)
        while i > 0 and float(up_vel[i]) >= thresh_v:
            i -= 1
        floor_cy = float(cy_s[i0])
        while i > 0 and float(cy_s[i]) < floor_cy - 0.03 * h:
            i -= 1
        onset_t = i / DETECT_FPS
        end_lim = min(n - 1, peak_i + int(round(2.5 * DETECT_FPS)), skip1)
        j = int(peak_i)
        returned = False
        while j < end_lim:
            if float(cy_s[j]) >= floor_cy - 0.05 * h:
                returned = True
                j = min(n - 1, j + int(round(1.0 * DETECT_FPS)))
                break
            j += 1
        if not returned:
            j = end_lim
        fitted = _fit_clip(onset_t, j / DETECT_FPS, duration, peak_i / DETECT_FPS)
        if fitted is None:
            continue
        start, end = fitted
        windows.append((start, end, "puxo (barra amarela)"))
        if len(windows) >= MAX_CLIPS_PER_FILE:
            break
    return windows


def _windows_from_center(
    center: np.ndarray, bot: np.ndarray, duration: float
) -> list[tuple[float, float, str]]:
    """Still → 0.6–3s center-column burst → quieter. Black plates / no yellow rims."""
    n = int(center.size)
    if n < 8:
        return []
    cen_s = _smooth(center, max(1, int(round(0.25 * DETECT_FPS))))
    bot_s = _smooth(bot, max(1, int(round(0.25 * DETECT_FPS))))
    look = max(1, int(round(1.5 * DETECT_FPS)))
    skip0, skip1 = _search_bounds(n, duration)
    region = cen_s[skip0:skip1]
    if region.size < 4:
        return []
    med = float(np.median(region))
    mad = float(np.median(np.abs(region - med)))
    abs_min = max(med + 1.5 * mad * 1.4826, 3.5)

    idx: list[int] = []
    scores: list[float] = []
    onset_at: list[int] = []
    for i in range(max(1, skip0), min(n - 1, skip1)):
        if not (cen_s[i] >= cen_s[i - 1] and cen_s[i] >= cen_s[i + 1]):
            continue
        if cen_s[i] == cen_s[i - 1] and cen_s[i] == cen_s[i + 1]:
            continue
        peak_v = float(cen_s[i])
        if peak_v < abs_min:
            continue
        half = 0.50 * peak_v
        a = i
        while a > skip0 and float(cen_s[a]) >= half:
            a -= 1
        b = i
        while b < skip1 - 1 and float(cen_s[b]) >= half:
            b += 1
        core = (b - a) / DETECT_FPS
        if core < (LIFT_MIN_S - 0.08) or core > (BURST_MAX_S + 0.45):
            continue
        thresh = 0.32 * max(peak_v, 1e-6)
        on = i
        while on > 0 and float(cen_s[on]) >= thresh:
            on -= 1
        sl = cen_s[max(0, on - look) : max(1, on)]
        still_on = float(np.median(sl)) if sl.size else 0.0
        if still_on > max(3.8, 0.58 * peak_v):
            continue
        if peak_v / (still_on + 0.5) < 1.55:
            continue
        after0 = min(n, b + 1)
        after1 = min(n, b + int(round(1.2 * DETECT_FPS)))
        if after1 > after0:
            after_m = float(np.median(cen_s[after0:after1]))
            if after_m > 0.85 * peak_v:
                continue
        if _is_camera_approach(i, cen_s, bot_s, n, duration):
            continue
        idx.append(i)
        scores.append(peak_v)
        onset_at.append(on)

    if not idx:
        return []
    mx = max(scores)
    keep = [k for k in range(len(idx)) if scores[k] >= REL_PEAK_FRAC * mx]
    min_dist = int(round(MIN_ATTEMPT_GAP_S * DETECT_FPS))
    picked: list[int] = []
    for k in keep:
        if all(abs(idx[k] - idx[p]) >= min_dist for p in picked):
            picked.append(k)

    windows: list[tuple[float, float, str]] = []
    for k in picked:
        peak_i = idx[k]
        peak_v = float(cen_s[peak_i])
        on = onset_at[k]
        onset_t = on / DETECT_FPS
        sl = cen_s[max(0, on - look) : max(1, on)]
        base = float(np.median(sl)) if sl.size else 0.0
        end_lim = min(n - 1, peak_i + int(round(2.5 * DETECT_FPS)), skip1)
        j = int(peak_i)
        returned = False
        while j < end_lim:
            if j > peak_i + int(round(0.4 * DETECT_FPS)) and float(cen_s[j]) <= max(
                base * 1.4, 1.4
            ):
                returned = True
                j = min(n - 1, j + int(round(0.8 * DETECT_FPS)))
                break
            j += 1
        if not returned:
            j = end_lim
        fitted = _fit_clip(onset_t, j / DETECT_FPS, duration, peak_i / DETECT_FPS)
        if fitted is None:
            continue
        start, end = fitted
        windows.append((start, end, "puxo (movimento vertical)"))
        if len(windows) >= MAX_CLIPS_PER_FILE:
            break
    return windows


def detect_lifts(path: Path, duration: float | None = None) -> list[tuple[float, float, str]]:
    """Encontra janelas de levantamento. Sem puxo → lista vazia (não inventa corte, sem leftover)."""
    if duration is None:
        duration = probe_duration(path)
    if duration > MAX_DURATION_S:
        raise ProcessError(
            f"Vídeo longo demais ({duration:.0f}s). Máximo {int(MAX_DURATION_S)}s por arquivo."
        )
    frames = _read_detect_frames(path, duration=duration)
    if frames.shape[0] < 8:
        return []
    cy_raw, counts, yellow_ok = _yellow_cy(frames)
    windows: list[tuple[float, float, str]] = []
    if yellow_ok:
        cy = _interp_nan(cy_raw)
        if np.isfinite(cy).all():
            skip0, skip1 = _search_bounds(int(cy.size), duration)
            if skip1 > skip0:
                span = float(np.nanmax(cy[skip0:skip1]) - np.nanmin(cy[skip0:skip1]))
            else:
                span = 0.0
            # Static yellow (lights / floor / wall) is not a bar. Motion handles black plates.
            if span >= MIN_TRAVEL_FRAC * float(DETECT_H) * 0.50:
                cy_s = _smooth(cy, max(1, int(round(SMOOTH_S * DETECT_FPS))))
                up_vel = np.zeros_like(cy_s)
                up_vel[1:] = -(cy_s[1:] - cy_s[:-1])
                windows = _windows_from_yellow(cy_s, up_vel, duration)
    if not windows:
        cen, bot = _center_column_mad(frames)
        windows = _windows_from_center(cen, bot, duration)
    kept: list[tuple[float, float, str]] = []
    for start, end, reason in windows:
        anchor = start + 0.65 * PREF_CLIP
        if _keep_window(start, end, duration, anchor):
            kept.append((start, end, reason))
    windows = kept
    if len(windows) > MAX_CLIPS_PER_FILE:
        windows = windows[:MAX_CLIPS_PER_FILE]
    return windows


def cut_clip(src: Path, dst: Path, start: float, end: float) -> None:
    """Letterbox 720x1280 (keep the athlete). Autorotate. No center-crop of portrait iPhone."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    has_a = probe_has_audio(src)
    dur = max(0.05, float(end) - float(start))
    vf = (
        "scale=720:1280:force_original_aspect_ratio=decrease,"
        "pad=720:1280:(ow-iw)/2:(oh-ih)/2:black"
    )
    cmd = [
        FFMPEG,
        "-y",
        "-i",
        str(src),
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{dur:.3f}",
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
    ]
    if has_a:
        cmd += ["-c:a", "aac", "-b:a", "96k"]
    else:
        cmd += ["-an"]
    cmd.append(str(dst))
    r = _run(cmd, timeout=CUT_TIMEOUT_S)
    if r.returncode != 0 or not dst.exists() or dst.stat().st_size < 500:
        raise ProcessError("Falha ao cortar o clipe. Tenta outro trecho/arquivo.")


def _hhmmss(start: float) -> str:
    t = int(max(0, round(start)))
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}{m:02d}{s:02d}"


def process_files(
    inputs: list[tuple[Path, str]], job_id: str | None = None
) -> dict:
    if not inputs:
        raise ProcessError("Nenhum vídeo pra processar.")
    job_id = safe_job_id(job_id) if job_id else uuid.uuid4().hex[:12]
    try:
        cleanup_old_jobs()
    except Exception:
        pass
    job_dir = job_dir_for(job_id)
    job_dir.mkdir(parents=True, exist_ok=True)
    for leftover in job_dir.glob("clip_*.mp4"):
        leftover.unlink(missing_ok=True)

    stored: list[tuple[Path, str, str]] = []
    for i, (src, name) in enumerate(inputs, start=1):
        if not src.exists():
            raise ProcessError(f"Arquivo de entrada não encontrado: {name}")
        if not probe_is_video(src):
            raise ProcessError(f"Isso não parece um vídeo ({name}). Envia MP4, WebM ou MOV.")
        dest = job_dir / f"input_{i:02d}.mp4"
        src_res, dest_res = src.resolve(), dest.resolve()
        if src_res != dest_res:
            shutil.copy2(src, dest)
        stored.append((dest, name, f"input_{i:02d}.mp4"))

    clips_meta: list[dict] = []
    sources: list[dict] = []
    n = 0
    used_names: set[str] = set()
    for dest, name, src_key in stored:
        duration = probe_duration(dest)
        if duration > MAX_DURATION_S:
            raise ProcessError(
                f"Vídeo longo demais ({duration:.0f}s). Máximo {int(MAX_DURATION_S)}s por arquivo."
            )
        windows = detect_lifts(dest, duration)
        safe_name = _safe_display_name(name)
        sources.append(
            {"name": safe_name, "duration": round(duration, 3), "lifts": len(windows)}
        )
        for start, end, reason in windows:
            n += 1
            filename = f"clip_{n:02d}.mp4"
            cut_clip(dest, job_dir / filename, start, end)
            dl = f"lpo-{_hhmmss(start)}.mp4"
            if dl in used_names:
                dl = f"cortai-lpo-{n:02d}.mp4"
            used_names.add(dl)
            clips_meta.append(
                {
                    "n": n,
                    "source": src_key,
                    "source_name": safe_name,
                    "file": filename,
                    "start": start,
                    "end": end,
                    "duration": round(end - start, 3),
                    "reason": reason,
                    "download": dl,
                }
            )

    payload: dict = {
        "id": job_id,
        "clips": clips_meta,
        "sources": sources,
    }
    if not clips_meta:
        payload["message"] = EMPTY_MSG
    (job_dir / "clips.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return payload


def process_file(src: Path, job_id: str | None = None) -> dict:
    return process_files([(src, src.name)], job_id=job_id)


def load_job(job_id: str) -> dict | None:
    try:
        job_dir = job_dir_for(job_id)
    except ProcessError:
        return None
    p = job_dir / "clips.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def clip_path(job_id: str, n: int) -> Path | None:
    try:
        job_dir = job_dir_for(job_id)
    except ProcessError:
        return None
    job = load_job(job_id)
    if not job:
        return None
    for c in job.get("clips", []):
        if int(c["n"]) == int(n):
            raw = Path(str(c.get("file") or ""))
            # basename only — never trust stored path segments
            name = raw.name
            if not name or name != raw.as_posix() or ".." in name:
                return None
            if not name.startswith("clip_") or not name.endswith(".mp4"):
                return None
            p = (job_dir / name).resolve()
            if not str(p).startswith(str(job_dir.resolve()) + "/") and p != job_dir.resolve():
                return None
            return p if p.is_file() else None
    return None


def with_urls(payload: dict) -> dict:
    jid = payload["id"]
    clips = []
    for c in payload.get("clips", []):
        item = dict(c)
        item["url"] = f"/api/jobs/{jid}/clips/{c['n']}"
        clips.append(item)
    out: dict = {
        "id": jid,
        "clips": clips,
        "sources": payload.get("sources", []),
    }
    if payload.get("message"):
        out["message"] = payload["message"]
    return out
