# 许可证清单与限制（开源复用）

政策：生产路径只用 Apache-2.0、MIT、BSD、NVIDIA Open Model License、SAM License 的组件。非商用（NC）、GPL/AGPL 的组件不进入生产路径，除非使用者另行取得授权。所有新模型都是可选组件、默认关闭；缺失时自动回退到原有实现（见 `configs/default.yaml` 的 `vision:`）。

下载规则：只用公开地址；每个文件固定仓库版本并用 sha256 校验（`src/wbs/vision/registry.py`）；门控仓库只报告、不申请、不下载。镜像（ModelScope、hf-mirror、清华 PyPI、南京大学 PyTorch 镜像）只改变传输来源，文件必须与固定的 sha256 一致。

## 已下载并在本机跑通

| 组件 | 用途 | 代码许可证 | 权重许可证 | 状态 |
|---|---|---|---|---|
| PySceneDetect 0.7.1 | 切镜 | BSD-3-Clause | — | 主环境（`--no-deps` 安装，避免 opencv-python 与 headless 冲突） |
| TransNetV2（soCzech） | 切镜 | MIT | MIT（官方权重的 PyTorch 转换版，取自 `Sn4kehead/TransNetV2`） | 30 MB 权重；在工作环境导出 ONNX，主环境用 onnxruntime 推理 |
| Grounding DINO tiny（IDEA-Research） | 文本提示主体检测框 | Apache-2.0 | Apache-2.0 | 689 MB |
| SAM 2.1 hiera tiny（Meta） | 由检测框得到主体掩码 | Apache-2.0 | Apache-2.0 | 156 MB（SAM 3 门控时的公开替代） |
| Depth Anything 3：DA3-SMALL / DA3-BASE / DA3METRIC-LARGE | 多视图相机与深度、米制尺度 | Apache-2.0 | Apache-2.0 | 137 MB / 541 MB / 1.34 GB |
| CenterFace ONNX（随 deface 发布） | 人脸检测，与 YuNet 取并集 | MIT | MIT | 7.3 MB；OpenCV DNN 推理 |
| PyTorch 2.11 cu128 / torchvision 0.26 | 视觉工作环境 | BSD-3-Clause | — | `.venv-vision` |
| transformers、huggingface_hub、onnx、onnxruntime、e3nn、addict、moviepy、pycolmap、trimesh、omegaconf、einops、scipy | 工作环境依赖 | Apache-2.0 / MIT / BSD | — | `requirements/vision-worker.txt` |

## 只做了适配器，未下载或未安装

| 组件 | 用途 | 许可证 | 原因 / 启用方式 |
|---|---|---|---|
| SAM 3 / 3.1（Meta） | 文本提示分割与跟踪 | SAM License（允许商用，有使用限制，见原文） | **Hugging Face 门控仓库，按要求未申请访问、未下载**。获批后把权重放到 `models/sam3/`，`--subject-model sam3` 即可启用 |
| VGGT-1B-Commercial | 多视图相机与深度（备选） | VGGT Commercial License | 门控仓库，未申请；几何求解改用 DA3 / MapAnything |
| MapAnything（`facebook/map-anything-apache`） | 多视图米制重建 | 代码与该权重均 Apache-2.0 | 需另装 `mapanything` 包和 4.9 GB 权重：`setup_vision.ps1 -MapAnything` |
| MegaSaM | 动态视频相机与深度 | 代码 Apache-2.0 | 默认深度先验 UniDepth 为 CC BY-NC，不能直接用；平台只导入其 NPZ 结果（`--geometry npz --track`），自行运行时须把深度先验换成 DA3 可商用权重 |
| CameraBench | 运镜分类体系 | 论文（NeurIPS 2025）；仓库 CC-BY-4.0 | 只采用其运镜类别体系（反推标签与正向分镜词表共用，署名见下节）；微调的 Qwen2.5-VL 权重许可证未核实，不使用。`--camera-motion llm` 走已配置的模型提供方 |
| VBench | 时序评测（主体一致性、运动平滑、闪烁） | Apache-2.0 | 未安装；装上后体验报告自动写入分数。其评测子模型各有许可证，商用前需逐一核对 |
| FiftyOne | 分组审片与标签 | Apache-2.0 | 未安装；未安装时 `wbs review export` 写可编辑清单 |
| Infinigen | 室内布局 | BSD-3-Clause | 未安装；`wbs forward import-layout` 可导入其 `.blend` |
| NVIDIA Cosmos-Transfer2.5 | 白模 + 深度/分割/边缘 → 真实画面 | NVIDIA Open Model License（允许商用；不得绕过其安全护栏等，见原文） | 需 Hopper 架构、约 80 GB 显存的服务器；`video.provider: cosmos_transfer` 对接自建 NIM，接口字段须按部署版本核对 |
| Wan2.2 VACE-Fun A14B | 同上，本机 480p 试验 | Apache-2.0 | 权重未下载；`video.provider: wan_vace` 对接本机 ComfyUI。社区 GGUF 量化与 LightX2V 加速各有仓库，使用前核对许可证 |

## 借鉴结构、未复制代码

- Dramatron（Apache-2.0）：分层生成顺序（一句话 → 人物 → 情节节拍 → 场景）。
- ViMax（MIT）：编剧/分镜智能体的分工与修订回路。
- FilmAgent（论文；代码仓库现名 VideoClaw，HITsz-TMG/VideoClaw，MIT）：评审-修正-验证回路，最多 2 轮；镜头词表附使用条件与顺序规则的写法
  （开场先交代环境、同景别不连用等），用于分镜提示词和镜头语法检查的规则表述。
- PythonRobotics（AtsushiSakai/PythonRobotics，MIT）：路线编译的方法（转角圆弧过渡、按加减速上限的梯形速度曲线）；`src/wbs/forward/motion.py` 为自写实现，只用标准库。
- CameraBench（sy77777en/CameraBench，CC-BY-4.0）：`taxonomy/camera_language.yaml` 的运镜与其基本运镜类别对应。署名：Lin 等，
  “Towards Understanding Camera Motions in Any Video”（CameraBench）。景别、角度与 30°、180° 等镜头语法为通行电影语法；节奏区间取自参考导演卡。
- DirectorSKILL（MIT）：只参考结构；其“知名导演风格”模块不采用。

## 评估过、本轮未接入

| 组件 | 用途 | 许可证 | 原因 |
|---|---|---|---|
| ShotVL-7B（Vchitect/ShotBench） | 视觉模型读故事板，核对景别、角度、运镜 | 代码仓库无许可证；ShotVL-7B 权重 Apache-2.0（3B 版底座为非商用许可，不用） | 需下载约 15 GB 并量化到 12 GB 显存；先用规则层（`qc/grammar.py`）核对 |
| GenDoP | 文本生成相机轨迹 | 权重 Apache-2.0；训练数据 DataDoP 未说明许可证 | 只生成自由机位，不以人物路线为条件；先用规则化的 rig（`forward/rigs.py`） |
| camera_shakify（Blender 插件） | 实拍手持抖动 | GPL-3.0 | 不进入生产路径；手持感用自写的程序化响应层 |

## 不进入生产路径

| 组件 | 原因 |
|---|---|
| DA3-LARGE、DA3-GIANT | CC BY-NC 4.0（非商用） |
| `facebook/map-anything`（非 -apache 版） | CC BY-NC 4.0 |
| UniDepth（MegaSaM 默认深度先验） | CC BY-NC 4.0 |
| GVHMR、WHAM 模型、TRAM | 依赖 SMPL（非商用）；GVHMR 本身禁止商用 |
| CoTracker | CC BY-NC 4.0 |
| Ultralytics YOLO | AGPL-3.0 |
| evo、plyfile | GPL-3.0。DA3 加载时会 import，但推理不调用（只用于“对齐到给定相机”和 PLY 导出）；工作环境不安装，`worker_main.py` 用占位模块代替，调用即报错 |
| pillow_heif 二进制包 | 内置 GPL 的 x265；只被 DA3 的网页演示使用，不安装 |

外部工具：ffmpeg 与 Blender 以独立进程调用，许可证取决于所用的二进制发行版（Blender 为 GPL，作为外部程序使用）。
