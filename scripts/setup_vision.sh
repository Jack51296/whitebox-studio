#!/usr/bin/env bash
# Optional vision environment (off by default; every backend falls back to the built-in implementation).
# Same steps as scripts/setup_vision.ps1. Mirrors only change the transport; model files must match the pinned sha256.
#   PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple TORCH_INDEX=https://mirrors.nju.edu.cn/pytorch/whl/cu128 \
#   MODEL_ENDPOINT=modelscope ./scripts/setup_vision.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHON=${PYTHON:-python3}
PYPI_INDEX=${PYPI_INDEX:-https://pypi.org/simple}
TORCH_INDEX=${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}
TORCH_VERSION=${TORCH_VERSION:-2.11.0+cu128}
TORCHVISION_VERSION=${TORCHVISION_VERSION:-0.26.0+cu128}
MODEL_ENDPOINT=${MODEL_ENDPOINT:-huggingface}
PROFILE=${PROFILE:-default}
export PYTHONIOENCODING=utf-8

MAIN=.venv/bin/python
[ -x "$MAIN" ] || { echo "main environment .venv not found (docs/deployment.md)"; exit 1; }
echo "==> main environment: vision extras + PySceneDetect"
"$MAIN" -m pip install -i "$PYPI_INDEX" -e ".[vision]"
"$MAIN" -m pip install -i "$PYPI_INDEX" --no-deps "scenedetect>=0.7"

WORKER=.venv-vision/bin/python
[ -x "$WORKER" ] || "$PYTHON" -m venv .venv-vision
echo "==> worker: torch $TORCH_VERSION"
"$WORKER" -m pip install --upgrade -i "$PYPI_INDEX" pip
"$WORKER" -m pip install "torch==$TORCH_VERSION" "torchvision==$TORCHVISION_VERSION" --index-url "$TORCH_INDEX" --extra-index-url "$PYPI_INDEX"
"$WORKER" -m pip install -i "$PYPI_INDEX" -r requirements/vision-worker.txt
"$WORKER" -m pip install -i "$PYPI_INDEX" --no-deps --ignore-requires-python "depth-anything-3==0.1.1"
if [ "${MAPANYTHING:-0}" = "1" ]; then
  "$WORKER" -m pip install -i "$PYPI_INDEX" "git+https://github.com/facebookresearch/map-anything.git"
fi

if [ "${SKIP_MODELS:-0}" != "1" ]; then
  "$MAIN" scripts/fetch_models.py --profile "$PROFILE" --endpoint "$MODEL_ENDPOINT"
  [ "${MAPANYTHING:-0}" = "1" ] && "$MAIN" scripts/fetch_models.py --models map-anything-apache --endpoint "$MODEL_ENDPOINT"
fi
"$MAIN" -m wbs.cli vision prepare
"$MAIN" -m wbs.cli vision status
