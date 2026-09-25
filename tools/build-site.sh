#!/usr/bin/env bash
# Monta o site estático em $1 (padrão _site): app + ffmpeg.wasm self-hosted (do npm, versões fixas) + sample.
# Pré-requisitos: npm install já rodado; tests/fixtures/sample.mp4 gerado (python3 tests/make_fixtures.py).
set -euo pipefail
cd "$(dirname "$0")/.."
OUT=${1:-_site}
rm -rf "$OUT"
mkdir -p "$OUT/vendor/ffmpeg" "$OUT/vendor/util" "$OUT/vendor/core"
cp index.html "$OUT/"
cp -r css js "$OUT/"
cp node_modules/@ffmpeg/ffmpeg/dist/esm/*.js "$OUT/vendor/ffmpeg/"
cp node_modules/@ffmpeg/util/dist/esm/*.js "$OUT/vendor/util/"
cp node_modules/@ffmpeg/core/dist/esm/ffmpeg-core.js node_modules/@ffmpeg/core/dist/esm/ffmpeg-core.wasm "$OUT/vendor/core/"
cp tests/fixtures/sample.mp4 "$OUT/sample.mp4"
printf '%s\n' "Cortaí — build $(date -u +%Y-%m-%dT%H:%M:%SZ) ${GITHUB_SHA:-local}" > "$OUT/version.txt"
echo "site em $OUT:"; find "$OUT" -type f | sort
