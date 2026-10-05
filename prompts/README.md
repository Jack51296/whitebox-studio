# 提示词库

所有提示词带 YAML 头，由 `wbs.prompts` 按 id 调用。

`id`、`version`、`kind`、变量、模板逻辑和正文保持验证基线。`verbatim` 返回原始正文，`template` 使用 Jinja2 严格变量模式。

公开版本仅整理来源元数据，移除内网 URL；正文 SHA256 与基线核验一致。修改正文必须升版本并运行 `pytest tests/test_prompts.py`。

V2V 系统提示词来自 `skills/generate-whitebox-v2v-package/references/planner-sp.md` 和 `output-schema.md`。
