<#
Optional vision environment (off by default; every backend falls back to the built-in implementation).

  1. main .venv : pip install -e .[vision] + PySceneDetect (--no-deps, keeps opencv-python-headless)
  2. .venv-vision: torch/torchvision (CUDA wheels, cu128 for RTX 50-series) + requirements/vision-worker.txt
                   + depth-anything-3 (--no-deps --ignore-requires-python)
  3. model weights: scripts/fetch_models.py (public URLs, pinned sha256; gated repos are never requested)
  4. TransNetV2 ONNX export + worker self-test

Public mirrors are allowed and change nothing but the transport (packages are resolved normally, model files
must match the pinned sha256), e.g. on networks where pypi.org / download.pytorch.org / huggingface.co are slow:
  .\scripts\setup_vision.ps1 -PypiIndex https://pypi.tuna.tsinghua.edu.cn/simple `
                             -TorchIndex https://mirrors.nju.edu.cn/pytorch/whl/cu128 -ModelEndpoint modelscope
#>
param(
    [string]$Python = "py -3.13",
    [string]$PypiIndex = "https://pypi.org/simple",
    [string]$TorchIndex = "https://download.pytorch.org/whl/cu128",
    [string]$TorchVersion = "2.11.0+cu128",
    [string]$TorchvisionVersion = "0.26.0+cu128",
    [string]$ModelEndpoint = "huggingface",
    [ValidateSet("default", "full")][string]$Profile = "default",
    [switch]$MapAnything,
    [switch]$SkipModels
)
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo
$env:PYTHONIOENCODING = "utf-8"

function Step($text) { Write-Host "==> $text" -ForegroundColor Cyan }
function Check($what) { if ($LASTEXITCODE -ne 0) { throw "$what failed (exit $LASTEXITCODE)" } }

$main = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $main)) { throw "main environment .venv not found; install the project first (docs/deployment.md)" }
Step "main environment: vision extras + PySceneDetect"
& $main -m pip install -i $PypiIndex -e ".[vision]"; Check "pip install -e .[vision]"
& $main -m pip install -i $PypiIndex --no-deps "scenedetect>=0.7"; Check "scenedetect"

$worker = Join-Path $repo ".venv-vision\Scripts\python.exe"
if (-not (Test-Path $worker)) {
    Step "create .venv-vision"
    Invoke-Expression "$Python -m venv .venv-vision"; Check "venv"
}
Step "worker: torch $TorchVersion / torchvision $TorchvisionVersion"
& $worker -m pip install --upgrade -i $PypiIndex pip; Check "pip upgrade"
& $worker -m pip install "torch==$TorchVersion" "torchvision==$TorchvisionVersion" --index-url $TorchIndex --extra-index-url $PypiIndex; Check "torch"
Step "worker: requirements/vision-worker.txt + depth-anything-3"
& $worker -m pip install -i $PypiIndex -r requirements\vision-worker.txt; Check "worker requirements"
& $worker -m pip install -i $PypiIndex --no-deps --ignore-requires-python "depth-anything-3==0.1.1"; Check "depth-anything-3"
if ($MapAnything) {
    Step "worker: mapanything (git, Apache-2.0 weights facebook/map-anything-apache)"
    & $worker -m pip install -i $PypiIndex "git+https://github.com/facebookresearch/map-anything.git"; Check "mapanything"
}

if (-not $SkipModels) {
    Step "model weights ($Profile, endpoint $ModelEndpoint)"
    & $main scripts\fetch_models.py --profile $Profile --endpoint $ModelEndpoint; Check "fetch_models"
    if ($MapAnything) { & $main scripts\fetch_models.py --models map-anything-apache --endpoint $ModelEndpoint; Check "fetch map-anything" }
}
Step "TransNetV2 ONNX export + worker self-test"
& $main -m wbs.cli vision prepare; Check "wbs vision prepare"
& $main -m wbs.cli vision status
