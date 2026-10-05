# 运行手册

## 1. 安装（Windows 本机）

```powershell
cd .\whitebox-studio
python -m venv .venv
.\.venv\Scripts\pip install -e ".[dev]"
.\scripts\fetch_tools.ps1        # 便携 Blender 5.1.2、FFmpeg、YuNet 人脸模型（公开下载 + sha256 校验）
.\.venv\Scripts\wbs doctor       # 三个工具都有路径即可
```

Linux 服务器用 `docker/Dockerfile`（见 `docs/deployment.md`）。工作区默认 `./workspace`，可用 `WBS_WORKSPACE` 指到大容量磁盘。

## 2. 三条正向路线

```powershell
# 结构树批量：50 条，叙事/运动各半，2 路并行渲染
wbs forward batch --batch B20260927 --n 50 --seed 1 --workers 2

# 故事驱动（规划模型默认 mock）：分层链 前提 → 人物 → 事件链 → 空间 → 分镜，逐层校验；真实模型下再评审修正最多 2 轮
wbs forward story --batch S20260927 --job S001 --brief "两人抢一只滚动的箱子，最后一起抬走" --content-class motion
wbs forward story ... --mode single        # 旧的单次规划
wbs forward story --batch S20260927 --job S002 --brief "…" --plan D:\剧本\story_plan.json   # 导入外部撰写的 StoryPlan（不调用规划模型，契约与渲染前闸门照常）

# 看片回路：先把当前交付备份到 versions/vNN，再按意见修订并重渲
wbs forward revise workspace\batches\S20260927\S001 --notes "转折不清楚" --level story   # 从事件链重做
wbs forward revise workspace\batches\S20260927\S001 --notes "第二镜更近" --level shots   # 只重做分镜

# 长镜头规划
wbs forward longtake --batch L20260927 --job L001 --brief "从门厅一路走到屋顶平台" --zones 6

# [可选] 导入 Infinigen / 任意 .blend 室内布局为体块（保留相机与主体路线，重跑动态闸门报告碰撞）
wbs forward import-layout <任务目录> --layout D:\布局\room.blend --offset 0,0
```

故事任务额外产出 `story_plan.json`、`reports/故事评审.json`（逐层记录、评审历史、机械检查）；每次渲染都写 `reports/导演卡对照.json`（与参考样例的镜头数、镜长、事件数、字段对照，只作参考）。

故事规划的写法（外部撰写 StoryPlan 时同样适用）：
- 人物路线优先写 `routes`（途经点、到达时刻、停留），平台按主体种类的运动上限编译成关键帧；做不到时报出哪一段、需要多大加速度。手写的 `paths` 原样使用，超限会被渲染前闸门拦下。
- 机位优先写 `rig`（跟拍、前导、并行、推拉、升降、环绕、俯视、主观、车载），距离按景别与焦距推算并整体避让体块；每镜写 `framing / angle / move`，平台实测后核对。
- 铺垫事件写 `targets` 或 `focus_region`，检查它们在铺垫镜头里是否看得清。
- 场景默认自动加尺度参照（参照柱、车道虚线、集装箱标准箱、楼层线，`configs/default.yaml` 的 `forward.dressing`；单片用 `"dressing": false` 关闭）。
- 渲染前检查写 `reports/制作数据检查.json`（运动与穿模，判定）和 `reports/镜头语法与可读性.json`（镜头语言、语法、节奏、铺垫，默认只警告）。

每条任务目录（`workspace/batches/<批次>/<任务>/`）：

| 文件 | 说明 |
|---|---|
| `scene.json` | 唯一数据源 |
| `<标题>_白模参考.mp4` | H.264、yuv420p、无音轨 |
| `<标题>.blend` | 可编辑工程（相机名 `CAMERA_Sxx`，主体 `ACTOR_x`） |
| `分镜总览.jpg`、`故事板/` | 每镜中间帧（不足 4 镜补阶段帧） |
| `剧本与分镜导演卡.txt`、`视频续作提示词.txt`、`导演注释.vtt` | 参考交付格式 |
| `路线俯视图.png` | 只用于核对 |
| `prompts/` | 白模控制层、V2V 渲染提示词、提示词版本引用 |
| `reports/` | 全部检查报告（见 `docs/qc-gates.md`） |
| `audit/` | Blender 逐帧实测（主体/相机/屏幕框、体块包围盒、遮挡比例） |
| `render/blender.log` | Blender 日志 |
| `模型导出/` | 可选：`wbs export` 或 `wbs batch finish --export-models` 生成的 FBX、GLB、`镜头切换.csv` |

## 3. 反推

```powershell
wbs reverse analyze --batch R20260927 --job R001 --video D:\素材\clip.mp4 --license "自有拍摄"
wbs reverse solve workspace\batches\R20260927\R001 --subject person --lens 28
wbs reverse online-package workspace\batches\R20260927\R001 --subject person   # 只导出，不上传
```

可选视觉后端（默认关闭；缺依赖或权重时自动回退，原因写进 `analysis.json` / `job.json`）：

```powershell
wbs reverse analyze ... --cuts transnetv2 --subject-model grounding_dino --prompt "person." --camera-motion rules
wbs reverse solve <任务目录> --geometry da3 --scale camera_height          # 或 --scale metric（DA3METRIC-LARGE）
wbs reverse solve <任务目录> --geometry npz --track D:\megasam\sgd_cvd_hr.npz # 导入外部 MegaSaM / 转换后的 COLMAP 结果
python scripts\reverse_benchmark.py                                        # 真值评测：切点 F1、占幅、相机误差
```

## 3.1 可选视觉环境

```powershell
.\scripts\setup_vision.ps1 -PypiIndex https://pypi.tuna.tsinghua.edu.cn/simple `
    -TorchIndex https://mirrors.nju.edu.cn/pytorch/whl/cu128 -ModelEndpoint modelscope   # 镜像只改传输，sha256 不变
wbs vision status            # 配置、依赖、模型校验、工作环境与 GPU、每个后端能否运行及原因
wbs vision fetch --profile full --endpoint modelscope
wbs vision prepare           # 自检 + 导出 TransNetV2 ONNX
```

torch 模型在独立的 `.venv-vision` 里运行（主环境只通过 JSON/NPZ 文件与它交换数据），不影响主环境依赖。门控仓库（SAM 3 等）只报告、不申请。

## 4. 白模转真人（Skill v3）

```powershell
wbs v2v run workspace\batches\B20260927\B20260927_0001 --max-images 4     # 规划 → 生图 → 打包
wbs v2v plan <任务目录> --plan-file D:\规划\SP输出_v3.json --max-images 5   # 导入外部撰写的 v3 规划（不调用规划模型，仍做 v3 契约校验）
wbs v2v images <任务目录>; wbs v2v package <任务目录>                       # 分步执行生图与打包（未确认付费时生图为占位图）
wbs v2v submit workspace\batches\B20260927\B20260927_0001                  # 默认只写手工提交说明
wbs v2v run --inputs D:\交付\某片四文件 --batch V20260927 --job V001        # 任意四文件目录
wbs v2v compare <任务目录> --result D:\平台返回\成片.mp4                     # 双路对比视频 + 成片时序证据
wbs render <任务目录> --passes depth,seg,edge                                # [可选] 控制通道，供 Cosmos-Transfer / Wan VACE
```

真实生图/规划：`configs/providers.yaml` 改为 `openai_compatible`，在环境变量配置网关地址与密钥，先 `--dry-run`，确认后 `--confirm-paid`。

## 5. 复核、收尾、入库

```powershell
wbs review mark <任务目录> --field sampled_visual --value passed --reviewer 张三 --note "故事板与关键帧已看"
wbs review mark <任务目录> --field normal_speed_viewing --value passed --reviewer 张三
wbs review mark <任务目录> --field curation --value adopted --reviewer 李四
wbs review export B20260927           # 每条一组（原片/白模/成片）：装了 FiftyOne 写分组数据集，否则写可编辑清单
wbs review sync B20260927 --reviewer 张三   # 标签回写台账，只处理新增标签
wbs batch finish B20260927            # 交付清单、生产与复核记录、阅读说明、ZIP（不覆盖旧包）
wbs batch finish B20260927 --export-models   # 同上，打包前为每条已渲染任务导出 FBX/GLB 与 镜头切换.csv
wbs export <任务目录或批次> --formats fbx,glb  # 单独导出模型；已有 模型导出/ 时另建 模型导出_<时间>/
wbs registry scan; wbs dashboard --open
wbs cost report --batch B20260927     # md | csv | json
```

## 6. 续跑与重做

- 中断后直接重跑同一命令：台账里成功且输入未变的步骤会跳过。
- 强制重渲某条：`wbs render <任务目录> --force`；只重跑质检：`wbs qc <批次>`。
- 某条失败不影响整批；原因在 `job.json` 的 `error` 与 `render/blender.log`。

## 7. 常见问题

| 现象 | 处理 |
|---|---|
| `找不到 blender` | 运行 `scripts/fetch_tools.ps1` 或设置 `WBS_BLENDER` |
| Blender 渲染失败 | 看 `render/blender.log` 末尾；无 GPU 的 Linux 需要 Mesa（Dockerfile 已装） |
| 剪辑检查的 `visual_detection` 有漏报/多报 | 只作警告：判定以 Blender 实际激活相机为准；多报的切点请人工看前后帧（可能是闪跳） |
| 静态闸门失败 | 看 `reports/static_gate/*.jpg` 三图证据与 `reports/反推修正记录.json`；不要改阈值凑通过 |
| 故事规划报“路线做不到” | 按提示推迟到达时刻、减少急停或拉开途经点；车辆急弯前要留出减速距离 |
| 渲染前闸门报 `actor_accel` / `actor_lateral` / `actor_yaw_rate` | 手写 `paths` 改成 `routes`，或放宽时间、加大弯道半径；上限在 `qc.dynamic_gate.kinds`，确需调整时改配置并说明依据 |
| `镜头语法与可读性.json` 有警告 | 按 detail 改 rig、景别或切点；要让它阻塞交付，把 `qc.grammar.enforce` / `qc.readability.enforce` 设为 true |
| FBX 在 Blender 里导入后差一帧 | 导入时把 Animation Offset 设为 0；其他软件按 `镜头切换.csv` 的文件帧号对齐 |
| 真实调用被拒 | 未加 `--confirm-paid`、或超过 `configs/default.yaml` 的单条/批次预算 |
| 控制台中文乱码 | 设置 `PYTHONUTF8=1`，或在 Windows Terminal 中运行 |
| 选了新视觉后端但结果里写着 builtin / motion / heuristic | 自动回退了：看 `analysis.json` 的 `fallback_reasons` 或 `wbs vision status` |
| pypi.org / download.pytorch.org / huggingface.co 下载很慢 | 用公开镜像参数运行 `setup_vision.ps1`（文件仍按固定 sha256 校验） |
