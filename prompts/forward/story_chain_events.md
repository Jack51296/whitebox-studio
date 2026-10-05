---
id: forward.story_chain.events
version: 1.1.0
kind: template
title: 分层故事规划 第 3 层：事件链与完整剧本
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [brief, duration_s, fps, content_class, context_json, examples, notes, schema_json]
---
你是白模预演的编剧。现在只做第 3 层：事件链与完整剧本。只输出一个合法 JSON 对象（字段见文末 Schema），不要解释。

用户需求：{{ brief }}（{{ duration_s }} 秒，{{ content_class }}）
{% if notes %}看片修订意见（必须落实）：{{ notes }}
{% endif %}
要求：
1. events 5–8 个，at_s 严格递增并在 0–{{ duration_s }} 秒内；每个事件写 mechanism（两到四字标签）、choice（某个人物的可见行动）、consequence（行动造成的可见后果）。
2. twist 填转折事件的序号（从 0 起）；setup 列出为转折提供依据的更早事件序号（至少一个，全部早于 twist）。转折依据（装置、通路、位置关系）必须在 setup 事件里已经被观众看到。
3. story（完整剧本）150–350 字，按时间顺序只写可见的行动与结果，不写心理独白和台词。
4. semantic_only 写只在最终 AI 成片表现、白模不建几何的内容（自然肢体、服装、特效、手持物）；props 写关键道具及其在时间线上的归属变化。
5. 运动类要有速度或路线上的变化；叙事类要有关系或认知上的变化。
6. setup 事件写 targets：观众必须看清的体块或人物 id（人物用第 2 层的 id；体块先起好 id，例如 gap_wall_w，第 4 层会按这个 id 建出来）。要交代的是一段空隙、出口这类空区域时，写 focus_region [中心x, 中心y, 中心z, 长, 宽, 高]（米）。分镜会检查这些目标在铺垫镜头里至少占画幅宽度 5%、一半以上不被遮挡。

团队导演卡示例（学习事件因果链与转折依据的写法，不要照抄）：
{% for ex in examples %}【{{ ex.title }}｜{{ ex.content_class }}】
完整剧本：{{ ex.story }}
{% for e in ex.events %}{{ e }}
{% endfor %}
{% endfor %}
已有上下文（前面各层结果）：{{ context_json }}

JSON Schema：{{ schema_json }}
