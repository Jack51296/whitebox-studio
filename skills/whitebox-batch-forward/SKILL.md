---
name: whitebox-batch-forward
description: 按视频结构树批量生产白模视频（15 秒 / 24fps / 1920×1080 默认）：抽样控制信息 → scene.json → 渲染前动态闸门 → Blender 无头渲染 → 分层质检 → 批次交付。适用于“批量造白模数据”“按结构树生成 N 条白模”等请求；不用于故事短片（用 whitebox-story-studio）或真实视频反推（用 whitebox-reverse-gated）。
---

# 结构树批量白模

来源：[D2] Blender 批量造白膜数据 · 正向构造（视频结构树 → 白模控制层 prompt → Blender 建模 → V2V 渲染提示词）。
执行器是本仓库的 `wbs` 命令行；本 Skill 只说明怎么用、按什么规则判断，不要另写建模脚本。

## 什么时候用

- 用户要一批白模视频做数据或做 V2V 参考，按“镜头形式 / 主体 / 颜色 / 时代 / 视角 / 运镜 / 叙事或运动”组合。
- 用户给了条数、比例、主体范围或随机种子。

## 步骤

1. 确认环境：`wbs doctor`（Blender、ffmpeg、ffprobe 都要有路径）。缺工具时运行 `scripts/fetch_tools.ps1`，不要索取任何权限。
2. 预览抽样（可选）：`wbs taxonomy sample --n 10 --seed 1`。结构树在 `taxonomy/structure_tree.yaml`，约束会自动排除不成立的组合（无主体不跟拍、焦点转移至少两个主体、一镜到底只能长镜头等）。
3. 生成并渲染：
   ```
   wbs forward batch --batch B20260927 --n 50 --seed 1 --narrative-ratio 0.5 --workers 2
   ```
   - 每条先过渲染前动态闸门（相机/主体/体块逐帧采样），不过就换种子重生成（最多 5 次），`批次清单.xlsx` 的“渲染前动态闸门”列如实记录。
   - 只要场景不渲染：加 `--no-render`，之后 `wbs render B20260927 --workers 2`。
4. 查看结果：每条任务目录里有 `*_白模参考.mp4`、`分镜总览.jpg`、`故事板/`、`剧本与分镜导演卡.txt`、`视频续作提示词.txt`、`导演注释.vtt`、`路线俯视图.png`、`prompts/白模控制层.txt`、`prompts/V2V渲染提示词.txt`、`reports/`。
5. 收尾：`wbs batch finish B20260927`（交付清单、生产与复核记录、阅读说明、ZIP；旧 ZIP 不覆盖）。
6. 入库与看板：`wbs registry scan && wbs dashboard --open`。

## 判断规则（照做，不代填）

- `technical` 是程序检查；`sampled_visual`、`normal_speed_viewing`、`curation` 只能由看过的人用 `wbs review mark` 标记，未标记就保持 not_run / pending，汇报时照实说。
- 白模控制层只表达空间、主体、动作与相机，不写世界观、材质和渲染风格；“时代”只进 V2V 渲染提示词。
- 低精度：体块场景 + 刚体主体，只平移和绕竖轴转向，无面部、手指、服装褶皱、纹理、文字。
- 相机与主体、体块保持安全距离；动态闸门报告为空才算过。
- 失败的任务不影响整批；查看 `job.json` 的 error 与 `render/blender.log`，修正后 `wbs render <任务目录> --force`。
- 不上传、不推送任何数据；V2V 真实调用需要用户确认（见 generate-whitebox-v2v-package 与 docs/security-compliance.md）。
