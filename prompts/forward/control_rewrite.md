---
id: forward.control_rewrite
version: 1.0.0
kind: template
title: 白模控制层 prompt 改写（可选的 LLM 多样化）
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [control_prompt]
---
你是 3D 预演（previz）建模指令编写助手。请把下面的白模控制层 prompt 改写成一份更具体、可直接执行的 Blender 建模提示词。

必须遵守：
1. 保留全部数字（时长、帧率、帧数、分辨率、镜头数）和结构树控制信息，不增删镜头。
2. 只写空间、主体、动作、相机；不写世界观、材质、渲染风格、时代背景。
3. 保留全部白模规则（低精度、无肢体动画、单色体块、无景深与运动模糊、不穿模）。
4. 只输出改写后的提示词正文，不要解释。

白模控制层 prompt：
{{ control_prompt }}
