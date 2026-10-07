#!/usr/bin/env bash
# Downloads the speech models (about 700 MB, one-off) into models/.
# Whisper small.en gives accurate text; the NeMo CTC model gives word timings.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE=https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models
tmp=$(mktemp -d); mkdir -p models/whisper-small.en models/ctc-conformer-small
curl -L "$BASE/sherpa-onnx-whisper-small.en.tar.bz2" | tar -xj -C "$tmp"
cp "$tmp"/sherpa-onnx-whisper-small.en/small.en-{encoder,decoder}.int8.onnx "$tmp"/sherpa-onnx-whisper-small.en/small.en-tokens.txt models/whisper-small.en/
curl -L "$BASE/sherpa-onnx-nemo-ctc-en-conformer-small.tar.bz2" | tar -xj -C "$tmp"
cp "$tmp"/sherpa-onnx-nemo-ctc-en-conformer-small/{model.int8.onnx,tokens.txt} models/ctc-conformer-small/
rm -rf "$tmp"; echo "models ready"
