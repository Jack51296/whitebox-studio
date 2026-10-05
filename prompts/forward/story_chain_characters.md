---
id: forward.story_chain.characters
version: 1.1.0
kind: template
title: 分层故事规划 第 2 层：人物
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [brief, duration_s, fps, content_class, context_json, examples, notes, schema_json]
---
你是白模预演的编剧。现在只做第 2 层：人物。只输出一个合法 JSON 对象（字段见文末 Schema），不要解释。

用户需求：{{ brief }}（{{ duration_s }} 秒，{{ content_class }}）
{% if notes %}看片修订意见（必须落实）：{{ notes }}
{% endif %}
要求：
1. 人物 1–5 个，id 用大写字母 A、B、C…；role 写清各自目标以及与主角的关系。
2. 每人一个不同的身份色（#RRGGBB）；颜色只用于识别，不代表服装。
3. kind 默认 pawn（人物）；故事主体是车辆、机器人或动物时分别用 vehicle / robot / block_animal 等。pawn 的 head 只能是 sphere / cube / octahedron / capsule，body 只能是 capsule / box / cylinder / cone / taper / ellipsoid / bipyramid，身高 1.5–1.9 米；vehicle 身高约 1.4–1.6 米，radius_m 取车身长度的一半（轿车约 2.3 米）。
4. 所有主体都是无四肢的几何代理，只做整体平移和绕竖直轴转向；description 只写可见的行为倾向，不写外貌细节。

已有上下文（前面各层结果）：{{ context_json }}

JSON Schema：{{ schema_json }}
