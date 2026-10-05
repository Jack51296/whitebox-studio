# Agent 接入 Blender 白模搭建及 V2V 渲染

用 Agent 串联白模搭建、镜头准备、Blender 渲染、质检与 V2V 交付。支持批量运行、断点续跑和可追溯状态。

- **正向**：视频结构树批量 / 故事驱动短片 / 长镜头规划 → `scene.json`；故事里的路线和机位写意图，由平台按主体种类的
  运动上限和景别编译成关键帧，场景自动补尺度参照（参照柱、车道虚线、标准集装箱、楼层线）
- **反推**：已授权真实视频 → 抽帧、切镜、光流、占幅、结构线 → `scene.json` 草稿 → 静态闸门与修正；在线模型转换提交包
- **白模**：Blender 5.1.2 无头建场、逐帧关键帧、逐镜相机、Workbench/EEVEE 渲染、逐帧审计 → H.264 无音轨
- **质检**：渲染前闸门（运动上限、穿模、遮挡感知构图、镜头语法与节奏、铺垫可读性）、媒体/剪辑/景别/速度/朝向/构建一致性/多样性；
  四类状态如实记录
- **白模转真人**：遵循 Skill v3（四文件 → 规划 → 生图 → 绑定打包 → 提交，默认只导出）
- **运营**：SQLite 台账（幂等续跑）、成本台账与预算闸门、入库目录、双路对比看板、批次交付 ZIP、模型导出（FBX / GLB / 镜头切换表）

## 快速开始（Windows）

```powershell
git clone https://github.com/Jack51296/whitebox-studio.git
cd whitebox-studio
python -m venv .venv; .\.venv\Scripts\pip install -e ".[dev]"
.\scripts\fetch_tools.ps1                 # 便携 Blender 5.1.2 / FFmpeg / YuNet（公开下载 + sha256）
.\.venv\Scripts\wbs doctor

wbs forward batch --batch B1 --n 10 --seed 1 --workers 2      # 结构树批量：生成 + 渲染 + 质检
wbs forward story --batch S1 --job S001 --brief "两人抢一只滚动的箱子"
wbs forward longtake --batch L1 --job L001 --brief "从门厅一路走到屋顶平台" --zones 6
wbs reverse analyze --batch R1 --job R001 --video 素材.mp4 --license "自有拍摄"
wbs reverse solve workspace\batches\R1\R001
wbs v2v run workspace\batches\B1\B1_0001                        # Skill v3：规划 → 生图 → 打包（默认 mock）
wbs review mark workspace\batches\B1\B1_0001 --field normal_speed_viewing --value passed --reviewer 张三
wbs forward revise workspace\batches\S1\S001 --notes "转折不清楚" --level story   # 看片回路（先备份到 versions/）
wbs vision status                                                # 可选视觉后端（默认关闭，缺失自动回退）
wbs export workspace\batches\S1\S001                              # 导出 FBX / GLB / 镜头切换表（batch finish 可加 --export-models）
wbs batch finish B1; wbs registry scan; wbs dashboard --open; wbs cost report
```

验收：`python scripts/e2e_mock.py`（真实 CLI + 真实渲染 + mock 模型，覆盖全部路线、续跑与V2V 回归）；
`scripts/ci.ps1` 跑 lint、Schema 检查与测试。

## 安全默认值

- 所有模型调用默认 mock；真实调用需要 `--confirm-paid`（或 `WBS_CONFIRM_PAID=1`）、建议先 `--dry-run`，受单条/批次预算约束。
- 凭证只从环境变量读取，不保存、不打印；视频提交默认只导出手工说明。
- 源代码、运行规则、提示词与脱敏运行数据可公开；原始来源资料、生产数据和凭证不入库。
- 需要你提供的资源和后续优化项记在本仓库的 Issues 里。
- 反推素材必须登记授权；外发前人脸打码并人工抽检；不使用第三方影视片段。
- 人工复核（抽帧、正常速度观看、采用）只由人标记，程序不代填。

## 文档

| 文档 | 内容 |
|---|---|
| [docs/architecture.md](docs/architecture.md) | 架构、模块与关键设计 |
| [docs/runbook.md](docs/runbook.md) | 安装、各路线命令、目录说明、续跑、常见问题 |
| [docs/data-contracts.md](docs/data-contracts.md) | scene.json 等数据契约与约定 |
| [docs/qc-gates.md](docs/qc-gates.md) | 闸门、检查报告与四类状态 |
| [docs/cost-model.md](docs/cost-model.md) | 成本记账、单价、预算与确认 |
| [docs/security-compliance.md](docs/security-compliance.md) | 数据、凭证、素材与外部调用规则 |
| [docs/source-mapping.md](docs/source-mapping.md) | 流程 → 实现映射 |
| [docs/missing-components.md](docs/missing-components.md) | 拿不到的内容与需要你提供的资源 |
| [docs/licenses.md](docs/licenses.md) | 开源复用组件的许可证、限制与下载状态 |
| [docs/deployment.md](docs/deployment.md) | 单机、容器、多机与 CI |
| [docs/integrated-workflow.md](docs/integrated-workflow.md) | 完整运行流程 |
| [skills/README.md](skills/README.md) | 4 个 agent skill |

## 目录

```
configs/     默认配置、模型提供方、单价            prompts/   带版本的提示词（正文 + 模板）
src/wbs/     平台代码（见 docs/architecture.md）    taxonomy/  视频结构树、镜头语言词表
schemas/     JSON Schema                            skills/    agent skills（含原样 Skill v3）
scripts/     工具下载、CI、端到端验收、Schema 导出  data/      脱敏运行数据（导演卡结构与统计）
docker/      生产镜像                               tests/     单元与集成测试
workspace/   生产数据（不入库）                     tools/     本机工具（不入库）
```

## 公开版本一致性

流程、默认参数、质检规则、提示词正文和 Skill v3 与验证基线一致。21 份导演卡仅保留程序使用的字段；示例选择顺序和统计范围保持一致。原始来源全文、附件、视频、批次报告与内部链接不随公开版本分发。

真实模型输出受模型版本、随机性与提供方影响；mock 回归用于验证流程和契约，不代表真实生成效果。见 [发布验证](docs/public-release.md)。
