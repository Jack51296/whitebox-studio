# 流程与代码映射

| 流程 | 实现 |
| --- | --- |
| 结构树批量 | `taxonomy/`、`forward/procedural.py` |
| 故事、评审与修正 | `forward/story_chain.py`、`prompts/forward/story_chain_*.md` |
| 示例与对照 | `data/story-cards.json`、`forward/story_examples.py` |
| 长镜头 | `forward/longtake.py`、`models/longtake.py` |
| 正反向统一场景 | `models/scene.py`、`blender/kinematics.py` |
| 渲染与预检 | `blender/`、`qc/pregate.py`、`qc/grammar.py` |
| 反推与回退 | `reverse/`、`vision/` |
| V2V | `v2v/`、`skills/generate-whitebox-v2v-package/` |
| 台账、费用与交付 | `ledger.py`、`cost/`、`qc/finish.py`、`registry.py` |

外部开源组件的来源与许可见 `licenses.md`。原始来源全文、原始附件和生产记录不随发布分发。
