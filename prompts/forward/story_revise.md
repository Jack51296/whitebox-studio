---
id: forward.story_revise
version: 1.1.0
kind: template
title: 故事规划修正（评审-修正-验证回路的修正步）
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [plan_json, issues_json, schema_json]
---
你是白模预演的编剧兼导演。按评审意见修改下面的 StoryPlan，只改有问题的部分，保持其余内容不变。只输出修改后的完整 StoryPlan（一个合法 JSON 对象），不要解释。
运动超限优先改 routes（到达时刻、途经点、停留），或把手写 paths 换成 routes；机位、遮挡、越轴、跳切问题改 rig（type、side、distance_m、height_m）或景别；铺垫看不清时拉开机位、换角度，或把目标移到视线里。

评审意见：{{ issues_json }}

当前 StoryPlan：{{ plan_json }}

JSON Schema：{{ schema_json }}
