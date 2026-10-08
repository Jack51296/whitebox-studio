# 数据契约

JSON Schema 在 `schemas/`（由 `src/wbs/models/` 生成，`wbs schemas` 或 `scripts/export_schemas.py` 重新导出，CI 检查是否过期）。

## 约定

- 单位：米、秒、度；世界坐标 Z 向上、+Y 为北；主体 yaw=0 面向 +Y，`yaw = atan2(-vx, vy)`。
- 帧与时间：第 f 帧（1 基）对应成片时间 `t=(f-1)/fps`；镜头帧范围 `f0=round(start*fps)+1`、`f1=round(end*fps)`。
- 镜头边界必须对齐帧、首尾相接并覆盖 `[0, duration_s]`。
- 主体路径用**源秒**，相机用**成片秒**；可选 `time_map`（单调）把成片秒映射到源秒，所有主体共用，相机响应不累积。
- 方向一律带符号数值，数据里不写“左/右”。

## scene.json（`wbs.scene/1.0`，`schemas/scene.schema.json`）

| 字段 | 说明 |
|---|---|
| `schema`、`id`、`title` | 版本、任务 ID、标题 |
| `fps`、`duration_s`、`resolution` | 规格（批量默认 24 / 15 / 1920×1080） |
| `precision` | `low`（默认，几何代理）/ `medium`（需 FBX 库） |
| `palette` | `white` / `grey` / `identity`（身份色只用于区分主体） |
| `time_map` | `{interpolation: linear|monotone_cubic, knots: [[edit, source], ...]}` 或 null |
| `blocks[]` | `{id, shape: box|cylinder|sphere|cone|capsule|ramp|plane, center, size, rotation_deg, role, color, collision, label, group?}`；`group` 让拆分出的单元（如集装箱拆成的标准箱）仍能按原 id 被引用 |
| `actors[]` | `{id, kind: pawn|block_animal|block_bird|block_fish|vehicle|robot|prop|fbx, height_m, radius_m, head, body, color, path{keys:[[t,x,y,z]], interpolation: linear|cubic|smooth}, yaw_keys, flap_hz, max_yaw_rate_dps?}`；转向上限缺省取该种类的 `yaw_rate` |
| `shots[]` | `{id, start_s, end_s, lens_mm, title, action, framing, angle, move, camera{keys, interpolation, aim_keys | aim_actor+aim_offset, roll_keys, responses[]}}`；景别、角度、运镜取值见 `taxonomy/camera_language.yaml` |
| `events[]` | `{id, at_s, mechanism, choice, consequence, role: beat|setup|twist, targets[], focus_region?}`（导演卡事件行标“（铺垫）/（转折）”）；`targets` 为主体、体块 id 或 `group`，`focus_region` 为 `[中心x, 中心y, 中心z, 长, 宽, 高]`，供铺垫可读性检查 |
| `beats[]` | 路线与空间交接 `{start_s, end_s, condition, action, change}`，须在 0..duration 内；导演卡与续作提示词各写一段 |
| `render` | `{engine: workbench|eevee, background, lighting, depth_far_m?}`（`depth_far_m` 为深度控制通道的远端，默认 60） |
| `source`、`meta` | 来源（控制信息 / 需求 / 反推分析）与文本产物所需的说明；故事路线另有 `meta.rigs`（每镜 rig 编译摘要：距离、高度、是否整体避让）与 `meta.dressing`（各类尺度参照的数量） |

插值 `smooth`：三点斜率加 Fritsch–Carlson 单调约束的三次插值，路线编译器生成的密集关键帧（0.1 秒）使用它，匀加速段与编译结果一致。

相机响应层（手持感等）：`{clock: edit|source, start, end, translation_m[3], rotation_deg[3], frequency_hz[2], seed:int, attack_seconds?, decay_seconds?}`，
频带上限必须低于 0.45×fps，按移植的 `sample_response` 叠加到未修改的基准变换上。

## StoryPlan（`schemas/story_plan.schema.json`）

规划模型输出：`title, logline, content_class, duration_s, world, lighting, story, goal, obstacle, stakes, ending, characters[], props[], spaces[], blocks[], paths{角色: keys}, routes{角色: RouteSpec}, events[], twist, setup[], beats[], semantic_only, shots[{id, start_s, end_s, title, action, framing, angle, move, lens_mm, camera_keys, rig, aim_actor | aim_keys}], time_map, notes, dressing`。
`routes`（推荐）与 `paths` 每个人物恰好写一种。`RouteSpec`：`{waypoints[{x, y, z, at_s?, stop_s, speed_mps?}], start_s, start_moving, corner_radius_m?}`，
由 `forward/motion.py` 按该人物种类的运动上限（`qc.dynamic_gate.kinds`，按 0.9 余量规划）编译成 0.1 秒关键帧：转角倒圆（半径 ≥ v²/横向上限）、
弯道按横向与转向上限限速、前后两遍按加速与制动上限、按到达时刻二分求巡航速度、支持停留；做不到时抛 `RouteInfeasible`，写明哪一段、需要多大的加速度。
`rig`：`{type: static|pan|follow|lead|side_track|push|pull|crane|orbit|top_down|pov|mount, target, distance_m?, height_m?, lateral_m?, side: left|right, position?, arc_deg, rise_m, handheld: 0–1}`，
由 `forward/rigs.py` 编译：距离缺省按景别和焦距推算，高度按角度；整条机位碰到体块或人物时依次换侧、拉近、抬高，再逐键避让；`handheld` 写成相机响应层。
每镜 `camera_keys` 与 `rig` 至少写一个，写了 `camera_keys` 就按手写关键帧。
`dressing`（默认 true）：按 `forward.dressing` 自动加尺度参照：平均速度 > 5 m/s 的路线旁每 25 米一根参照柱（参与碰撞）、车辆路线直线段的车道虚线、
`container` 体块拆成 12.19 × 2.44 × 2.59 米标准箱（`group` 保留原 id）、高度 ≥ 6 米的 `building` 每 3.2 米一圈楼层线（后两类线不参与碰撞）；生成的体块写进 scene.json。
`blocks[]` 已带平台生成的参照（`POST_001`、`LANE_001`、`<id>__floorN`，例如由已加参照的街区布局得到的规划）时，按当前场景重新生成，不重复添加；已拆分的标准箱保留并照常计数。
`characters[]`：`{id, role, color, kind: pawn|vehicle|robot|block_animal|block_bird|block_fish|prop, head, body, height_m, radius_m, description}`；`radius_m` 缺省按 kind 取值（人物 0.35，车辆 2.3 即车长一半）。
`lighting`（可选）：时段、天气与主要光源，只写入 V2V 渲染提示词的“光线与质感”，留空时按时代默认；白模仍按明亮灰白渲染。
导演卡与续作提示词的代理说明按主体种类生成：全是人物时沿用参考原句，含车辆等其他种类时改写为对应的还原与声音描述。
事件链校验（与参考 `stories.py` 一致）：至少 3 个事件、时间严格递增、每个事件有选择与后果；`twist` 为转折事件下标；`setup` 非空且全部早于转折。
转换时镜头边界吸附到帧（`source.shots_snapped_to_frames` 记录是否发生）。

分层链（`forward/story_chain.py`）按层输出并逐层校验，最后合成 StoryPlan：
`premise{title, logline, world, lighting, goal, obstacle, stakes, ending}` → `characters{characters[]}` → `events{story, events[], twist, setup[], semantic_only, props[]}`
→ `space{spaces[], blocks[], paths, routes, beats[]}` → `shots{shots[], time_map, notes}`。空间层另查路线能否编译、事件 `targets` 是否都建出来；
分镜层另查 rig 目标是否为人物。`reports/故事评审.json`（`wbs.story_review/1.0`）记录
`mode, start, notes, layers[{layer, attempts, problems}], examples, critique[], mechanical_checks[], simulated`；修订时另记 `revision_of`。
`mechanical_checks` 除参考规则外，还把规划转成场景跑一遍渲染前检查（运动上限、穿模、遮挡、镜头语言、语法与节奏、铺垫可读性），全部作为警告交给评审步。
修订前的交付物复制到 `versions/vNN/`（含 `snapshot.json`），`job.json` 的 `revisions[]` 记每次的层级与意见。

## LongTakePlan（`schemas/longtake_plan.schema.json`）

`title, logline, duration_s, zones[{id, name, function, width_m, depth_m, turn: straight|left|right, props, height_m}], with_subject, lens_mm, camera_height_m, door_width_m, notes`。

## 反推 analysis.json（`wbs.reverse.analysis/1.1`）

`video{file,width,height,fps,frames,duration_s,audio_streams}`、`cuts{frames_1based,times_s,method,detector{backend,requested,fallback_reasons?,builtin_agrees?},diff_signal}`、
`subject_detection{backend,requested,fallback_reasons?,model?,prompt?,detected_frames?,error?}`、
`shots[{id,start_frame,end_frame,start_s,end_s,first_mid_last_frames,flow{zoom_total,pan_x_total,pan_y_total,roll_total_deg,per_frame[]},occupancy_samples,occupancy{first,mid,last},lines{first,mid,last},frame_files,camera_motion{labels[],labels_zh[],ambiguous[],basis: flow|geometry,evidence,source}}]`、
`camera_motion{backend,taxonomy,simulated}`、`frames{uniform,bursts,shots}`、`annotation`、`not_run`。
光流符号：`dx>0` 画面内容右移（相机左摇）、`dy>0` 下移（上仰）、`scale>1` 放大（前推）、`roll_deg>0` 逆时针。
检测后端给出掩码时写 `analysis/masks/mask_fNNNNN.png`，光流按掩码（外扩 5 像素）遮蔽主体，否则按外扩 15% 的框。

## 相机轨迹 camera_track.npz

`frames[int32]`（0 起帧号）、`shot[int32]`、`c2w[N,4,4]`（OpenCV 约定：x 右、y 下、z 前；相机到世界）、`K[N,3,3]`（像素，对应 `meta.image_size`）、
可选 `depth[N,h,w]`（float16，z 深度，与 c2w 同单位）、`conf[N,h,w]`、`meta`（JSON：`source, metric, convention, image_size, metric_scales?`）。
也接受 MegaSaM 的 `sgd_cvd_hr.npz`（`cam_c2w, intrinsic, depths`；帧号按 `stride` 推算，镜头按切点分配）。

## 可选模型 models/manifest.json

`models{名称: {repo, revision, license, fetched_at, files{文件: {sha256, size, stamp, pinned}}}}`；固定值在 `src/wbs/vision/registry.py`，
`worker_selftest.json` 缓存视觉工作环境的包与 GPU 自检结果。

## V2V：SP输出_v3.json

完全按 `skills/generate-whitebox-v2v-package/references/output-schema.md`：顶层 `status, image_prompts, video_prompt, issues`；每图 16 字段；
anchors 8 字段；文生图不传任何图；局部图恰好一个主帧且在最前；`@图N` 连续；`shot_ids`、`video_usage.shot_id` 与视频正文引用三者一致；
视频提示词以“将无声白模预演替换为真人实拍，”开头。机械校验实现见 `src/wbs/v2v/contract.py`。

提交包（`v2v/项目_提交包/`）与 execution.md 一致：`视频1_白模.mp4`、`图N_*.png`、`视频渲染提示词.txt`、`提交清单.json`、`创作资料/`。
`提交清单.json` 另记 `files_complete`、`simulated_images`；`package_ready = files_complete 且图片非 mock`，只表示文件齐全。
任务有控制通道时，提交包另含 `控制通道/{depth,seg,edge}.mp4`，清单 `controls{名称: 相对路径}`（Cosmos-Transfer / Wan VACE 后端使用）；
自建后端返回的成片写为 `成片_cosmos_transfer.mp4` / `成片_wan_vace.mp4`。

## 模型导出（`wbs export`、`wbs batch finish --export-models`）

从任务的 `.blend` 导出到 `模型导出/`；目录里已有文件时改写到 `模型导出_<时间>/`，旧导出不覆盖。

- `<标题>.fbx`：米制单位（`FBX_SCALE_UNITS`），网格、相机、空物体，动画逐帧烘焙、只给有动画的通道打键；第 f 帧落在 `f/fps` 秒。
  Blender 自带的 FBX 导入器默认会偏一帧，导入时把 Animation Offset 设为 0。
- `<标题>.glb`：整场合成一段动画（所有主体与相机同一条时间线），强制逐帧采样；相机保留视场角。
- `镜头切换.csv`（UTF-8 带 BOM）：`镜头, 相机, 起始帧（文件帧号）, 结束帧（文件帧号）, 成片起始秒, 成片结束秒, 焦距mm, 景别, 角度, 运镜`，
  由 scene.json 生成；时间线上的切镜标记不会随 FBX/GLB 导出，按此表建切换轨道。
- `model_export.json`（`wbs.model_export/1.0`）：`blender_version, files, dropped_options, frames, fps, objects, cameras`；`dropped_options` 记当前 Blender 不认识而被略过的导出参数。
- `job.json` 的 `model_export{folder, files, exported_at}` 记最近一次导出。

## 任务元数据 job.json

`job_id, batch_id, route (forward_batch|forward_story|forward_longtake|reverse|v2v_only), title, status, pregate, static_gate, qc_status, review_*, v2v_*, model_export, source_license, updated_at`。

## 报告

见 `docs/qc-gates.md`。文件名沿用参考交付包：`media-check.json`、`制作数据检查.json`、`景别对照.json`、`速度测量.json`、
`experience-report.json`、`diversity-review.json`、`storyboard-frames.json`、`交付清单.json`、`生产与复核记录.json`。

`制作数据检查.json`（`wbs.qc.dynamic/1.1`）新增 `motion`（逐主体源秒峰值：速度、加速、制动、横向、转向）、`warnings` 与 `warning_counts`（反推路线的运动超限只记在这里）。
新增：`镜头语法与可读性.json`（`wbs.qc.grammar/1.0`：`status, warning_count, enforce, camera_language[], grammar[], pacing, setup_readability[]`）、
`故事评审.json`、`导演卡对照.json`（`wbs.card_compare/1.0`：`generated`、`team_reference` 统计、`notes`）、`时序证据.json`（`wbs.qc.temporal/1.0`）、
`控制通道.json`（`wbs.control_passes/1.0`，视频在任务目录 `控制通道/`，分割图例 `seg_legend.json`：物体名 → 类别与 RGB）、
`布局导入.json`（`wbs.layout_import/1.0`）、`v2v/成片时序证据.json`；工作区级 `reports/审片导出_<批次>.json`（`wbs.review_export/1.0`）
与 `reports/reverse_benchmark_<时间>.{json,md}`（`wbs.reverse.benchmark/1.0`）。
