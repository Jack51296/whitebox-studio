---
id: v2v.planner_user
version: 1.0.0
kind: template
title: V2V 规划节点输入（四文件）
source: "发布版工作流提示词；正文与已验证基线一致"
variables: [job_id, video, overview_name, director_card, continuation, options]
---
任务 job_id：{{ job_id }}

四份同版素材：
1. 白模视频：{{ video.name }}（{{ video.width }}×{{ video.height }}，{{ video.fps }}fps，{{ video.duration_s }} 秒，{{ video.frames }} 帧）。需要的原帧由执行节点按 anchors 从该视频抽取。
2. 分镜总览图：{{ overview_name }}（已随本消息附上）。
3. 剧本与分镜导演卡：见下文。
4. 视频续作提示词：见下文。

用户选项：
- 生图模型：{{ options.image_model }}；视频模型：{{ options.video_model }}。
- 风格：{{ options.style }}；画幅：{{ options.aspect }}；图片上限：{{ options.max_images }}。
{% if options.adaptation %}- 用户明确的改编：{{ options.adaptation }}
{% endif %}
请按规划 SP 与输出数据契约 v3，只输出 SP输出_v3.json（一个合法 JSON 对象，四个顶层字段 status、image_prompts、video_prompt、issues）。

===== 剧本与分镜导演卡 =====
{{ director_card }}

===== 视频续作提示词 =====
{{ continuation }}
