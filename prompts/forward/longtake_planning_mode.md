---
id: forward.longtake.planning_mode
version: 1.0.0
kind: verbatim
title: 复杂连续长镜头 / Spatial Blocking + Camera Path 规划模式
source: "发布版工作流提示词；正文与已验证基线一致"
imported_at: 2026-09-27
---
复杂连续长镜头 / Spatial Blocking + Camera Path 规划模式。 不要直接生成最终镜头，首先将整个场景理解为一个统一、连续、具有真实三维尺度的空间，建立完整的 Scene Geometry、人物 Blocking 和 Camera Blocking。根据我提供的场景描述，先把场景拆分为若干连续连接的 Scene Zones，例如入口、走廊、楼梯、房间、大厅、室外区域等，每个区域必须具有明确的相对位置、尺寸、朝向、入口、出口和连接关系，禁止为了镜头方便而改变建筑拓扑或让空间发生跳跃。随后生成一张俯视 / 鸟瞰式 Spatial Map，采用简洁的三维灰盒、建筑剖视或 dollhouse cutaway 方式表现整个场景，清楚显示墙体、门、楼梯、主要家具、人物初始位置以及不同区域，并使用 01 / 02 / 03... 标记 Scene Zones。然后在鸟瞰图上规划一条唯一且连续的摄影机路径 Camera Path，以清晰曲线贯穿整个空间，标记 C0 / C1 / C2 / C3... 摄影机关键节点、摄影机朝向和主要关注对象；路径必须符合真实摄影机能够移动的空间，不能穿墙、瞬移、跳跃、重置位置或产生不可能的转向。整个最终镜头必须是 one continuous take / single unbroken shot / zero cuts，从第一帧到最后一帧共享同一世界坐标、同一空间布局和同一人物连续状态。摄影机运动需要使用真实电影摄影语言组合，包括 dolly forward / backward、truck left / right、arc、orbit、pan、tilt、pedestal、crane、boom、handheld follow、Steadicam follow，但所有动作必须自然衔接，不得突然改变速度、焦距、机位或方向。人物在空间内具有独立但连续的 blocking，人物运动与摄影机路径互相配合，人物不能瞬移、复制、消失、交换位置或在遮挡后改变身份。必须维护严格的 spatial persistence / temporal persistence / identity persistence / object permanence：房间位置、门窗、墙面、家具、道具、人物服装、人物外貌、灯光方向及所有空间关系在整个长镜头中保持一致。对于复杂穿门、转角、绕柱、进入另一个房间等运动，必须提前规划转弯半径、遮挡关系和摄影机净空，优先通过真实空间运动完成，不允许依赖隐藏剪辑；即使主体短暂被墙体、人物或前景遮挡，摄影机轨迹也必须连续。镜头总时长改为 15 秒，镜头节奏应更紧凑，但依然保持清晰的空间交代、人物调度和镜头运动逻辑；镜头速度应具有电影摄影中的惯性，加速、减速、转向均使用平滑曲线，不做机械匀速滑动。首先输出 ① Scene Zones 空间划分；② Bird's-eye / Dollhouse 鸟瞰空间图；③ Camera Route 摄影机路径；④ Camera Keyframes；⑤ Character Blocking；⑥ 15 秒连续镜头时间轴；⑦ 最终用于视频生成的完整 Long-take Prompt。 在完成空间规划之前不要生成最终镜头。，人要简易人没有动作，像那种棋子，简易，场景和人物做颜色区分，
