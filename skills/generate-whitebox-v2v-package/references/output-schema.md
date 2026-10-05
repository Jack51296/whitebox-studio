# 输出数据契约 v3

本格式由 v2 迁移，保留四个顶层字段及大部分逐图字段。新增生成模式和设计自由；把生成后的 `review_checks` 替换为生成前 `prompt_constraints`。旧执行器需要适配，不能直接按 v2 的“所有场景都传帧、验图通过才继续”运行。

## 规划结果

`SP输出_v3.json` 是一个合法 JSON 对象，只有四个顶层字段：

| 字段 | 类型与意义 |
|---|---|
| `status` | `complete`、`partial`、`needs_input`；表示规划完整程度 |
| `image_prompts` | 本任务生图任务数组，按 `@图1` 起连续编号 |
| `video_prompt` | 一份完整的逐镜视频提示词字符串，换行使用 JSON 转义 |
| `issues` | `{type, shot_ids, description, handling}` 数组，三项文字为字符串，shot_ids 为整数数组 |

`complete` 仅为规划完成。`partial` 保留已能编写部分并指出缺失；不因没有源片全景、没有全片看片或不验图而变为 partial。`needs_input` 用于必需素材缺失到无法规划，图片数组为空、视频提示词为空；只索取缺少的四文件，不要求额外工程或 JSON。

## 每个生图任务：16个字段

| 字段 | 规则 |
|---|---|
| `id` | 字符串，建议 `img_01`；只用字母、数字、下划线、连字符，唯一且不含路径 |
| `ref` | `@图1` 等，与顺序对应 |
| `scene_ids` | 非空字符串数组，稳定的本片场景标识 |
| `name` | 具体图名，不含人物姓名 |
| `purpose` | 该图解决的美术或空间需求 |
| `shot_ids` | 使用本图的实际镜头顺序编号，正整数数组，不重复 |
| `image_type` | `scene_overview`、`shot_scene`、`subject_asset` |
| `generation_mode` | **`text_to_image` 或 `image_to_image`**，控制是否向生图接口传图 |
| `space_source` | 下述空间与设计对象 |
| `appearance_sources` | 外观依赖对象数组，无需依赖时 `[]` |
| `background_constraints` | `{required: 非空字符串数组, forbidden: 字符串数组}` |
| `state_scope` | `{shown: 字符串, video_rule: 字符串}`；都非空 |
| `video_usage` | 每项 `{shot_id: 正整数, inherit: 非空字符串数组, exclude: 非空字符串数组}` |
| `prompt_constraints` | 非空字符串数组，前置生成限制，**必须落实到 prompt 正文** |
| `prompt` | 可直接发送给生图节点的完整文字，不依赖“同上”或不可见外围字段 |
| `reference_image_ids` | 和 appearance_sources 的 image_id 严格同序，无重复，仅指本任务前面生成图 |

`shot_ids`、`video_usage.shot_id` 的集合及视频正文各镜对本图的引用必须相同。一张图可服务多镜，各镜借用范围分别限定。

### space_source

固定包含：

- `kind`：`scene_description`、`shot_frame` 或 `neutral_background`。
- `anchors`：实际要传给生图接口的白模帧数组。**不是规划模型看过的所有图片列表。**
- `preserve`：非空字符串数组，关键关系；不笼统锁死全部几何和摄影。
- `design_freedom`：非空字符串数组，主动重设计的造型、构件、材质、光照或展示构图。

白模元素的“可辨识特征／位置 → 实际语义 → 本图处理”沿用这些字段：需保留的语义与功能关系写入 `preserve`，真实重设计或移除后恢复的场地写入 `design_freedom`，防止误认或替代实物的限制写入 `prompt_constraints`，并在完整 `prompt` 中表达。特征只用于定位，不锁定白模外观；不新增语义映射字段。

路由：

| 默认任务 | generation_mode | kind | anchors | 其他图像依赖 |
|---|---|---|---|---|
| 主场景全景 | text_to_image | scene_description | 空 | 空 |
| 局部关键区域 | image_to_image | shot_frame | 恰好一个主帧，可加上下文 | 可空 |
| 独立非人物资产 | text_to_image | neutral_background | 空 | 空 |
| 需沿用已生成资产的设计 | image_to_image | neutral_background | 空 | 至少一个明确外观依赖 |

用户明确覆盖默认方式时按要求变更，mode 与实际输入仍必须一致。例如全景明确要求外观参考图，则可使用 image_to_image + scene_description，anchors为空，外观依赖非空；局部关系完全来自文字且用户要求文生图，可使用 text_to_image + scene_description。默认局部图仍按上表。

**text_to_image 的 anchors、appearance_sources、reference_image_ids 必须全部为空，生图请求 input_files 也必须为空。image_to_image 必须至少实际传入一张图。** 不存在“只当参考所以仍算文生图”的例外。

### 白模帧锚点

每项固定字段：

| 字段 | 类型与来源 |
|---|---|
| `id` | 稳定字符串，安全文件标识，同帧复用同 ID |
| `shot_id` | 已有分镜的顺序编号；主帧属于本图 shot_ids |
| `time_s` | 非负数或 null，首个解码画面为0的**成片时间**；只知道帧号时可null |
| `time_precision` | `exact` 或 `approximate`；依提供的定位信息填写 |
| `role` | `primary` 或 `context` |
| `description` | 该帧为何提供所需关系，及来自哪个总览格／文本条目 |
| `frame_number` | 非负整数或 null；来自文本，不能猜造 |
| `frame_number_base` | `0`、`1` 或 null；使用帧号时必须明确 |

按主帧、上下文的顺序排列；shot_frame 恰好一个 primary 且在最前。同 ID 必须对应同一定位信息。

局部图先按这些定位从视频抽帧，再写最终生图提示词。前置取帧清单由 agent 内部生成，只有 `anchors` 数组；不需要提前伪造完整 prompt 或最终规划状态。最终 v3 规划的锚点沿用前置 ID 与定位，实际帧路径仍留在执行清单，不新增本契约字段。

执行优先级：有明确基数的 frame_number → 转为从0起算的解码帧 index；否则使用 time_s。两种定位同时提供时应一致，不能混源时钟。无法确认编号基数时把 frame_number、frame_number_base 置null，选择镜内安全时间并标 approximate，说明不是精确复现某格；不要默认所有 `f0001` 都是一基，也不要索取第五份文件。

### 外观依赖

每项 `{image_id, inherit, exclude}`，后两者均为非空字符串数组。只继承明示的主体或场景设计、材料和光色；排除无关背景区域、未声明设施、展示机位、姿势以及未授权状态。允许声明借用本区域合理环境设计，但不能顺带迁入其他区域的终点或设施。依赖关系前置、无环；不跨任务引用。

### 视频提示词

无声白模转真人的第一句以 `将无声白模预演替换为真人实拍，` 开头，后接本片内容。按现有镜头顺序使用“镜头N｜起止时间｜事件／参考图／摄影／动作”结构，必要时在事件行保留源标签，例如“镜头9｜16.25—17.75秒｜止步反应（S08B）”。一个完整字符串可直接提取为 TXT。

## 执行数据另存

不得把路径、网络状态、验图字段随意加到规划顶层。外部清单记录 job_id、image_id、模式、输入顺序、实际取帧、接口任务 ID、输出路径、有效请求次数、网络重试和文件状态；**没有质量评分或视觉合格状态**。

批量每条视频各有一个规划 JSON 和结果清单，用 job_id 隔离相同 img_01。文件绑定保持图号，不因中途缺图把后续图片重新编号。
