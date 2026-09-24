# Cortaí

Sobe o treino cru de LPO. Acha cada levantamento e corta só o puxo — clipes verticais 720×1280 (Reels, TikTok, Shorts).

**No ar (URL fixa):** https://felandim.github.io/cortai/

**100% no navegador:** os vídeos não saem do aparelho. Não tem servidor, não tem upload.

## Como funciona

1. **Análise** (`js/detector.js`): o navegador abre o vídeo num `<video>` (já com a rotação do celular aplicada),
   amostra 10 frames/s num canvas 180×320 e calcula:
   - centroide vertical dos pixels amarelos (anilha bumper amarela), ignorando o topo (luzes);
   - fallback pra anilha preta: movimento (MAD de luma) na faixa central, "rajada depois de parado";
   - janela do clipe ancorada no **começo** do puxo (5–9 s, ~7 s, mais pré-roll que cauda); ignora os últimos 4 s do take.
   É um porte linha a linha de `legacy-server/processor.py`.
2. **Corte** (`vendor/` = ffmpeg.wasm 0.12, core single-thread self-hosted — sem CDN, sem COOP/COEP):
   letterbox 720×1280 (não corta o atleta), H.264 + AAC, `+faststart`.

Limite: 240 s por arquivo, até 8 arquivos por vez.

## Testes

```bash
npm install
python3 tests/make_fixtures.py          # gera fixtures (mp4) + expected.json com o detector Python (numpy + ffmpeg)
node tests/detector.test.mjs            # detector JS vs janelas do Python
bash tools/build-site.sh _site          # monta o site estático
npx playwright install chromium
(cd _site && python3 -m http.server 8099 &) ; CORTAI_URL=http://127.0.0.1:8099/ node tests/e2e.mjs
```

## Deploy

Todo commit em `main` roda `.github/workflows/deploy.yml`: gera fixtures, compara detector JS × Python, faz o build
(`tools/build-site.sh`: app + ffmpeg.wasm fixo do npm + sample), roda e2e headless e publica em `gh-pages`,
que o GitHub Pages serve em https://felandim.github.io/cortai/. Sem túnel, sem servidor.

`legacy-server/` guarda a versão antiga (FastAPI + ffmpeg) só como referência do detector — não é mais usada em produção.

## Licenças

`vendor/core` é o @ffmpeg/core (FFmpeg + x264, GPL-2.0-or-later); `vendor/ffmpeg` e `vendor/util` são MIT (ffmpeg.wasm).
