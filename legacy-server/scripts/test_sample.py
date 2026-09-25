#!/usr/bin/env python3
"""Gera sample.mp4 (3 levantamentos), processa, valida cortes 9:16 e multi-arquivo."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from processor import (  # noqa: E402
    FFPROBE,
    SAMPLE_PATH,
    generate_sample,
    process_file,
    process_files,
)


def probe_wh_dur(path: Path) -> tuple[int, int, float]:
    r = subprocess.run(
        [
            FFPROBE,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if r.returncode != 0:
        raise SystemExit(f"ffprobe falhou em {path}: {r.stderr}")
    data = json.loads(r.stdout)
    stream = data["streams"][0]
    w, h = int(stream["width"]), int(stream["height"])
    dur = float(data["format"]["duration"])
    return w, h, dur


def assert_clips(clips: list[dict], job_id: str, expect: int) -> None:
    if len(clips) != expect:
        raise SystemExit(f"esperava {expect} clips, veio {len(clips)}")
    job_dir = Path("/tmp/cortai-jobs") / job_id
    for c in clips:
        path = job_dir / c["file"]
        if not path.exists():
            raise SystemExit(f"faltando {path}")
        w, h, dur = probe_wh_dur(path)
        print(
            f"  clip {c['n']} [{c.get('source_name','')}] "
            f"{c['start']:.1f}–{c['end']:.1f}s ({c['duration']:.1f}s) "
            f"{w}x{h} reason={c['reason']}"
        )
        if not (5.0 <= float(c["duration"]) <= 10.0):
            raise SystemExit(f"duração fora de 5–10s: {c['duration']}")
        if dur <= 0:
            raise SystemExit(f"duração 0 em {path}")
        if w != 720 or h != 1280:
            raise SystemExit(f"aspecto inválido {w}x{h} em {path}")


def main() -> int:
    print("→ gerando sample.mp4")
    generate_sample(SAMPLE_PATH)
    size = SAMPLE_PATH.stat().st_size
    print(f"  {SAMPLE_PATH} ({size / 1024:.0f} KB)")
    if size > 1.5 * 1024 * 1024:
        raise SystemExit(f"sample grande demais: {size} bytes")
    w, h, dur = probe_wh_dur(SAMPLE_PATH)
    print(f"  source {w}x{h} {dur:.1f}s")
    if not (38 <= dur <= 42):
        raise SystemExit(f"sample duração inesperada: {dur}")
    if w != 1280 or h != 720:
        raise SystemExit(f"sample resolução inesperada: {w}x{h}")

    print("→ processando 1 arquivo")
    payload = process_file(SAMPLE_PATH, job_id="a1b2c3d4e5f6")
    clips = payload.get("clips") or []
    print(f"  job {payload['id']}  clips={len(clips)}")
    assert_clips(clips, payload["id"], 3)

    print("→ processando 2 arquivos (mesmo sample)")
    payload2 = process_files(
        [(SAMPLE_PATH, "treino_a.mp4"), (SAMPLE_PATH, "treino_b.mp4")],
        job_id="b2c3d4e5f6a7",
    )
    clips2 = payload2.get("clips") or []
    print(f"  job {payload2['id']}  clips={len(clips2)}")
    assert_clips(clips2, payload2["id"], 6)
    names = {c["source_name"] for c in clips2}
    if names != {"treino_a.mp4", "treino_b.mp4"}:
        raise SystemExit(f"source_names inesperados: {names}")
    sources = payload2.get("sources") or []
    if len(sources) != 2:
        raise SystemExit(f"esperava 2 sources, veio {len(sources)}")

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
