---
id: reverse.annotate_subjects
version: 1.0.0
kind: template
title: 反推主体标注（看图给出每镜头首/中/尾帧的主体框）
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [shots, auto_json]
---
你在做白模反推的主体占幅测量。附图按顺序是每个镜头的首帧、中间帧、尾帧：
{% for s in shots %}- {{ s.id }}：{{ s.start_s }}–{{ s.end_s }} 秒，图 {{ s.images | join('、') }}
{% endfor %}
请对每张图给出画面中主要运动主体（全部主体合并成一个外接框）的归一化坐标 [x0, y0, x1, y1]（左上角为 0,0，右下角为 1,1），看不到主体时给 null。同时判断主体类型（person / animal / bird / fish / vehicle / robot / product）和主体个数。

只输出 JSON：
{"subject_kind": "person", "subject_count": 1, "shots": {"S01": {"first": [x0, y0, x1, y1], "mid": [...], "last": null}}}

自动测量（运动残差，可能不准，仅供参考）：
{{ auto_json }}
