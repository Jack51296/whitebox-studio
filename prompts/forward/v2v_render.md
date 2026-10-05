---
id: forward.v2v_render
version: 1.1.0
kind: template
title: V2V 渲染 prompt（与白模控制层一一对应）
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [style_label, world, subjects, environment, camera_rule, lighting, long_take, vehicles]
---
将参考白模预演动画完整转换为{{ style_label }}视频。参考视频只提供主体运动、场景调度、镜头运动与切镜、构图和空间关系，不要保留低模、灰模、白模、blocking 或任何 3D 预演质感。

世界与时代：{{ world }}

主体：{% for item in subjects %}{{ item }}；{% else %}无主体，只表现环境与空间；{% endfor %}主体的比例、节奏、转向、加减速和整体运动路线严格参考原视频，{% if vehicles %}车轮随车速真实滚动，加减速、急刹与转向有真实的车身点头、侧倾和轮胎抓地反馈，不额外漂移甩尾，不悬空。{% else %}确保真实向前移动，不原地跑、不滑步。{% endif %}

场景：{{ environment }}保留原预演动画中的空间布局、障碍结构、通道和开阔区域关系，转化为真实材质与真实比例的环境，具有足够的纵深和真实世界尺度。

镜头：{{ camera_rule }}

光线与质感：{{ lighting }}

负面约束：
不要低模，不要灰模，不要白模，不要3D软件视窗感，不要塑料材质，{% if vehicles %}不要车轮不转，不要车辆漂浮或穿过障碍，{% else %}不要原地跑，不要滑步，{% endif %}不要主体变形，{% if long_take %}不要切镜，{% endif %}不要跳帧，不要镜头瞬移，不要剧烈抖动，不要穿模，不要相机穿墙，不要相机穿过主体身体。
