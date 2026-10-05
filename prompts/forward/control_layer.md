---
id: forward.control_layer
version: 1.0.0
kind: template
title: 白模控制层 prompt（由结构树抽样与 scene.json 生成）
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [spec, control, summary]
---
目标：
生成一段低精度 3D blocking 体块预演动画（白模），只表达空间、主体、动作与相机，不包含最终视频的世界观、材质和渲染风格。

视频规格：
时长：{{ spec.duration_s }} 秒
帧率：{{ spec.fps }}fps
总帧数：{{ spec.frames }} 帧
分辨率：{{ spec.resolution[0] }}×{{ spec.resolution[1] }}
渲染方式：快速预览渲染（Workbench 或同类），不使用 Cycles；无音轨。

结构树控制信息：
镜头形式：{{ control.shot_form_label }}{% if control.shot_count > 1 %}（{{ control.shot_count }} 个镜头）{% endif %}
主体状态：{{ control.subject_label }}
白膜体块颜色：{{ control.color_label }}
视角：{{ control.viewpoint_label }}
运镜：{{ control.camera_move_label }}
内容类别：{{ control.content_class_label }}

场景（只保留大结构）：
{{ summary.environment }}

主体：
{% for item in summary.subjects %}- {{ item }}
{% else %}- 无主体：画面只呈现空间与环境。
{% endfor %}
动作与位移：
{{ summary.motion }}

镜头与相机：
{% for item in summary.shots %}- {{ item }}
{% endfor %}
白模规则（强制低精度）：
- 所有主体由基础几何体拼成，无面部、无手指、无服装褶皱、无纹理、无文字。
- 人物只允许整体平移和绕竖直轴转向，不做肢体动画。
- {{ control.color_rule }}
- 柔和均匀照明，无景深、无运动模糊、无体积光。
- 主体始终清晰可见；相机与主体、体块保持安全距离，不得穿模。
