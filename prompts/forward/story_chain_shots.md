---
id: forward.story_chain.shots
version: 1.1.0
kind: template
title: 分层故事规划 第 5 层：分镜与相机
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [brief, duration_s, fps, content_class, context_json, examples, notes, schema_json, camera_vocabulary, rig_guide, pacing]
---
你是白模预演的摄影指导。现在只做第 5 层：分镜与相机。只输出一个合法 JSON 对象（字段见文末 Schema），不要解释。

时长：{{ duration_s }} 秒（{{ fps }}fps，{{ content_class }}）
{% if notes %}看片修订意见（必须落实）：{{ notes }}
{% endif %}
要求：
1. shots 从 0 秒开始、首尾相接、到 {{ duration_s }} 秒结束；镜长 1.3–5 秒为宜（一镜到底时只有一个镜头）；切点放在信息或行动变化处，不按等分切。{% if pacing %}节奏参照：{{ pacing }}{% endif %}
2. 每镜写 title（这一镜的信息任务）和 action（可见行动与结果）。setup 事件必须在转折之前被镜头交代清楚：事件 targets 或 focus_region 在铺垫镜头里至少占画幅宽度 5%，一半以上不被遮挡，常用远景、俯拍或让目标靠近机位来交代。
3. 每镜写 framing（景别）、angle（角度）、move（运镜），取值只用下面的 id；平台会实测画面，声明与实拍不符会被标出。
{{ camera_vocabulary }}
4. 机位优先用 rig 描述，平台据此编译关键帧并避让体块；只有需要精确控制时才写 camera_keys [t, x, y, z]（成片秒，落在本镜时间内），并配 aim_actor 注视人物或 aim_keys 注视空间点。焦距 20–50mm。
{{ rig_guide }}
5. 镜头语法：首镜用远景或大远景建立空间；景别至少两档，近景与特写合计不超过一半；相邻两镜拍同一主体且景别相同时，机位方向至少变 30°，否则换景别或换机位；机位保持在运动轴线同一侧，要越轴就在镜内完成，或插入贴近轴线的中性镜头；同一主体在切点前后的画面运动方向保持一致（追逐戏尤其要注意）。
6. 相机与人物至少保持 1 米、与体块至少 0.5 米，不穿墙、不穿过人物；主体在镜头里不能被体块长时间挡住。
7. notes 写一段摄影意图：整体的观看立场与镜头节奏如何服务转折。

已有上下文（前面各层结果）：{{ context_json }}

JSON Schema：{{ schema_json }}
