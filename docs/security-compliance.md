# 安全与公开范围

## 数据与凭证

- 可公开范围：运行代码、规则、提示词正文、Skill v3 与脱敏导演卡运行字段。
- 不随仓库分发：来源全文、原始归档、生产视频、批次数据、内网链接、账号与凭证。
- 生产工作区、模型、本机工具与配置覆盖只留在使用者设备。

## 凭证

- 模型网关地址与密钥只从环境变量或密钥系统读取（`configs/providers.yaml` 中只写变量名）。代码不保存、不打印、不写日志；
  `wbs doctor` 只显示变量是否存在。
- 不在命令行参数里传密钥（会进入 shell 历史与台账运行记录）。

## 外部调用

- 默认全部 mock；视频提交默认只导出手工说明（Skill v3：用户明确要求提交平台时才按已有授权执行）。
- 真实调用：先 `--dry-run`，再 `--confirm-paid`；受单条/批次预算上限约束；全部调用写台账可审计。
- 自建 Cosmos-Transfer 服务同样按付费调用处理（确认 + 预算）；本机 ComfyUI（Wan VACE）不出本机，但仍需在 `providers.yaml` 显式选择。

## 模型与依赖下载

- 只用公开地址；遇到需要申请权限的仓库一律**不申请**，只报告（SAM 3、VGGT-1B-Commercial 均为门控仓库，未申请、未下载）。
- 每个文件固定仓库版本与 sha256（`src/wbs/vision/registry.py`）；小文件的 sha256 先与服务器公布的 git blob id 核对后再固定。
  镜像只改传输来源，文件与固定值不符即删除并报错。
- 许可证政策见 `docs/licenses.md`：非商用（NC）与 GPL/AGPL 组件不进生产路径；DA3 加载时引用的 GPL 包（evo、plyfile）用占位模块代替、不安装。
- 模型与工作环境目录（`models/`、`.venv-vision/`）不入库。

## 素材

- 反推源视频必须登记授权（`--license`），记录在 `job.json` 与在线转换包 `提交清单.json`。
- 任何可能外发的视频先人脸打码（YuNet 检测 + 高斯模糊，[D7]；可用 `vision.faces: [yunet, centerface]` 并入 deface 的 CenterFace，
  两者的框取并集以减少漏检），并**人工抽检**：自动检测可能漏掉小脸、遮挡或大角度侧脸，
  报告中 `manual_check_required: true`，提交清单里“人脸打码抽检”初始为 pending。CenterFace 的检出效果尚未用有授权的含人脸素材评测。
- 不使用第三方影视片段，不收录针对具体影片的提示词（导入时已排除）；故事与世界观由需求给出。
- 在线转换的四宫格样板图只用本任务自己的白模渲染，不拿原片画面代替。

## 删除与覆盖

- 交付物不被静默覆盖：V2V 提交包、在线转换包重建时先把旧包改名为 `*_旧_<时间>`；批次 ZIP 每次新文件名。
- `wbs forward revise` 先把当前交付复制到 `versions/vNN/` 再重做；`wbs forward import-layout` 先把旧 `scene.json` 复制到 `versions/layout_<时间>/`。
- 程序只清理自己的中间产物（渲染 PNG 帧序列、控制通道帧序列）。

## 人工复核

程序不代填观感：`sampled_visual`、`normal_speed_viewing`、`curation` 只能由复核人用 `wbs review mark` 标记，记录复核人与时间。
