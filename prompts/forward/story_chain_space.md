---
id: forward.story_chain.space
version: 1.1.0
kind: template
title: 分层故事规划 第 4 层：空间、体块与人物路线
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [brief, duration_s, fps, content_class, context_json, examples, notes, schema_json, motion_limits]
---
你是白模预演的场景与调度设计。现在只做第 4 层：空间、体块和每个人物的路线。只输出一个合法 JSON 对象（字段见文末 Schema），不要解释。

时长：{{ duration_s }} 秒（{{ content_class }}）
{% if notes %}看片修订意见（必须落实）：{{ notes }}
{% endif %}
要求：
1. 米制世界坐标，+Y 为北，地面 z=0。先设计 spaces（每个空间一句功能说明），再用 blocks 搭出入口、出口、通道、遮挡和事件需要的装置（box / cylinder / ramp 等），体块之间相接或留缝，不互相嵌入。role 写清 building、container、wall 等用途。
2. 人物路线优先写 routes：每个人物给途经点 waypoints [{x, y, z, at_s, stop_s, speed_mps}]。at_s 是到达该点的源秒（不写时按 speed_mps 或默认速度行进），stop_s 是在该点停留的秒数；start_s 是出发时刻，start_moving 表示开场时已在运动。平台按下面的上限把路线编译成关键帧：转角自动倒圆、加减速不超限；做不到时会报出哪一段、需要多大的加速度，请据此调整到达时刻或途经点。只有需要逐帧精确控制的少数人物才写 paths（[t, x, y, z]，t 为源秒，覆盖 0 到 {{ duration_s }}，停顿用相同坐标的连续关键帧）。同一人物 routes 与 paths 只写一种。
3. 各种类运动上限（与渲染前闸门同一套数字）：
{{ motion_limits }}
   步行 1.2–2 米/秒、奔跑 4–7 米/秒；车辆过急弯前要先减速，并行时留出至少 1 米间距。路线不能穿过任何体块。
4. 每个事件发生时，相关人物必须在能完成该行动的位置；setup 事件里的装置或通路要在空间里真实存在，事件 targets 写到的体块 id 必须在 blocks 里用同一个 id 建出来。要交代的缝隙、通道留足宽度（车辆通过时至少比车宽多 1 米），并留出能看见它的视线，不要被高墙完全挡住。
5. beats（路线与空间交接）按时间写“条件 → 行动 → 可见变化”，与事件链对应，只描述白模里实际发生的路线和空间转换。
6. 只建必要结构；武器、特效、手持小物件不建几何。平台会自动补尺度参照：高速路线旁的参照柱、车辆路线的车道虚线、building 的楼层线、container 拆成标准箱，不必手工建这些。

已有上下文（前面各层结果）：{{ context_json }}

JSON Schema：{{ schema_json }}
