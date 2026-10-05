# 部署

## 单机（开发 / 小批量）

Windows：见 `docs/runbook.md`（便携 Blender + FFmpeg 放在 `tools/`，工作区在 `workspace/`）。

实测性能（RTX 5070，Workbench，AA 8）：15 秒 1920×1080（360 帧）约 50 秒/条。2 路并行时，4 条共 127 秒（含规划与质检）。
26 秒 1280×720 约 50 秒；质检每条 1–3 秒。

## Linux 服务器 / 容器

```bash
docker build -f docker/Dockerfile -t whitebox-studio:0.1.0 .
docker run --rm --gpus all -v /data/wbs:/workspace whitebox-studio:0.1.0 doctor
docker run --rm --gpus all -v /data/wbs:/workspace whitebox-studio:0.1.0 forward batch --batch B1 --n 50 --workers 2
```

- 镜像内有 Blender 5.1.2（blender.org 官方包，sha256 校验）、Debian FFmpeg、YuNet 人脸模型和 CJK 字体。
  以非 root 用户运行，工作区挂在 `/workspace`。
- Workbench/EEVEE 需要 OpenGL。在 GPU 机器上用 NVIDIA Container Toolkit（`--gpus all`）运行。没有 GPU 时，Mesa llvmpipe
  可以软件渲染，结果正确，但速度慢很多。
- **状态**：本机没有 Docker 和 WSL，镜像尚未在本机构建验证。第一次在服务器上构建后，请运行 `wbs doctor` 和
  `scripts/ci.sh --e2e` 验收。
- 在 KML 开发机上直接用 Linux 版 Blender 也可以：设置 `WBS_BLENDER`、`WBS_FFMPEG`、`WBS_FFPROBE`、`WBS_FACE_MODEL`
  与 `WBS_WORKSPACE` 即可。如果工作区放在 共享存储 共享盘上，不要对大目录做递归扫描；`wbs registry scan` 只遍历
  `batches/<批次>/<任务>` 两层目录。

## 可选视觉环境（GPU）

- `scripts/setup_vision.ps1`（Linux：`scripts/setup_vision.sh`）：主环境装 `.[vision]` 与 PySceneDetect（`--no-deps`），
  另建 `.venv-vision`（torch 2.11 cu128 适配 RTX 50 系；其他显卡改 `-TorchIndex` / `-TorchVersion`），装 `requirements/vision-worker.txt`
  与 depth-anything-3（`--no-deps --ignore-requires-python`），下载模型并自检。本机实测：RTX 5070 12 GB 自检通过。
- 网络慢时可用公开镜像（只改传输，文件按固定 sha256 校验）：本机实测 pypi.org 与 download.pytorch.org 约 0.05 MB/s、
  huggingface.co 约 1 MB/s；清华 PyPI 镜像约 44 MB/s、南京大学 PyTorch 镜像约 5 MB/s、ModelScope 约 15–30 MB/s。
- 显存：Grounding DINO tiny + SAM 2.1 tiny、DA3-BASE（每镜 24 帧）、DA3METRIC-LARGE 在 12 GB 上都能跑；MapAnything 需另装；
  Cosmos-Transfer2.5 需 Hopper 架构约 80 GB 的服务器；Wan2.2 VACE 在 12 GB 上只做 480p 试验。
- 工作环境路径可用 `vision.python` 或 `WBS_VISION_PYTHON` 指定，模型目录可用 `vision.models_dir` 或 `WBS_MODELS_DIR` 指到共享盘；
  模型目录在 共享存储 上时同样不要做递归扫描（平台只按登记的文件名逐个校验）。

## 多机与调度

- 台账是每个工作区一个 SQLite（WAL），同一台机器的多个进程可以安全并发写入。多台机器请各用各的工作区，不要共享同一个
  SQLite 文件；需要集中管理时再把 `ledger.py` 迁到服务端数据库（接口很小：runs / steps / usage / reviews 四张表）。
- 按批次切分给不同机器，例如 `--seed` 不同、`--batch` 不同，最后把各机的 `batches/` 汇总到同一个工作区，
  再运行 `wbs registry scan` 与 `wbs dashboard`。
- 定时任务：`wbs forward batch ...` 是幂等的，同一批次重复运行时，已经成功的步骤会跳过，中断后直接重跑即可续上。

## 配置分层

`configs/default.yaml` → `configs/local.yaml`（不入库）→ `WBS_CONFIG` 指向的文件 → 环境变量（`WBS_WORKSPACE`、
`WBS_BLENDER`、`WBS_FFMPEG`、`WBS_FFPROBE`、`WBS_FACE_MODEL`、`WBS_CONFIRM_PAID`、`WBS_VISION_PYTHON`、`WBS_MODELS_DIR`、
`WBS_COSMOS_ENDPOINT`、`WBS_COMFYUI_URL`）。模型凭证只放环境变量或密钥系统。

## CI

- `scripts/ci.ps1` / `scripts/ci.sh`：ruff、检查 JSON Schema 是否过期、pytest（没有 Blender 时相关测试自动跳过；
  `--fast` 跳过 Blender 测试）、`--e2e` 跑一次 mock 端到端（真实渲染）。
- 代码公开托管，真实渲染测试在具备 Blender 与 FFmpeg 的设备运行。
- 21 份脱敏导演卡运行数据在 `data/story-cards.json`；原始附件不随仓库分发。
