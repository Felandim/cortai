#!/usr/bin/env python3
"""Gera os vídeos de teste e as janelas esperadas pela versão Python (referência).

  python3 tests/make_fixtures.py   # precisa numpy + ffmpeg

Saídas em tests/fixtures/: sample.mp4 (barra branca, caminho "movimento vertical"),
yellow_portrait.mp4 (anilhas amarelas, retrato 720x1280), yellow_rotated.mp4
(mesmo vídeo gravado em paisagem com metadado de rotação 90°, como iPhone) e
expected.json com as janelas do detector Python.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "legacy-server"))
import processor  # noqa: E402

FIX = HERE / "fixtures"
FIX.mkdir(exist_ok=True)


def yellow_portrait(out: Path, rotate_store: bool = False) -> None:
    w, h, fps, dur = 720, 1280, 24, 30
    lifts = (6.0, 17.0)
    up, hold, drop = 1.0, 1.2, 0.3
    y_low, y_high = 1020, 430
    rng = np.random.default_rng(7)
    sw, sh = (h, w) if rotate_store else (w, h)
    cmd = [
        processor.FFMPEG, "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{sw}x{sh}", "-r", str(fps), "-i", "pipe:0",
        "-f", "lavfi", "-i", f"anoisesrc=color=white:amplitude=0.02:duration={dur}:sample_rate=44100",
        "-map", "0:v", "-map", "1:a", "-t", str(dur),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "48k", "-ac", "1", "-movflags", "+faststart", str(out),
    ]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    base = np.zeros((h, w, 3), dtype=np.uint8)
    base[:] = (38, 36, 34)
    base[1120:] = (70, 66, 60)
    yy, xx = np.mgrid[0:h, 0:w]
    for i in range(fps * dur):
        t = i / fps
        y = processor._bar_y(t, lifts, y_low, y_high, up, hold, drop)
        fr = base.copy()
        fr[max(0, y - 170):y + 260, 300:420] = (120, 100, 90)  # "atleta"
        fr[y - 6:y + 6, 40:680] = (190, 190, 195)  # barra
        for cx in (90, 630):
            m = (xx - cx) ** 2 + (yy - y) ** 2 <= 85 ** 2
            fr[m] = (232, 196, 28)
        noise = rng.integers(-4, 5, size=(h, w, 1), dtype=np.int16)
        fr = np.clip(fr.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        if rotate_store:
            fr = np.ascontiguousarray(np.rot90(fr, k=-1))  # guarda deitado
        p.stdin.write(fr.tobytes())
    p.stdin.close()
    if p.wait() != 0:
        raise SystemExit("ffmpeg falhou")


def main() -> int:
    processor.generate_sample(FIX / "sample.mp4")
    yellow_portrait(FIX / "yellow_portrait.mp4")
    tmp = FIX / "_rot_tmp.mp4"
    yellow_portrait(tmp, rotate_store=True)
    subprocess.run(
        [processor.FFMPEG, "-y", "-v", "error", "-display_rotation", "90", "-i", str(tmp),
         "-c", "copy", str(FIX / "yellow_rotated.mp4")],
        check=True,
    )
    tmp.unlink()
    expected = {}
    for name in ("sample.mp4", "yellow_portrait.mp4", "yellow_rotated.mp4"):
        path = FIX / name
        dur = processor.probe_duration(path)
        wins = processor.detect_lifts(path, dur)
        expected[name] = {
            "duration": dur,
            "windows": [{"start": s, "end": e, "reason": r} for s, e, r in wins],
        }
        print(name, round(dur, 2), wins)
    (FIX / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
