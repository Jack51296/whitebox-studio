"""White model → live action (白模转真人 Skill v3): plan → frames → images → bind/package → submit.

The vendored skill (skills/generate-whitebox-v2v-package) is the authority; this package implements
its three nodes as an executor. Only mechanical checks are made (JSON, files, decoding, numbering);
nothing here judges visual quality, and package_ready only means the files are complete.
"""

SKILL_DIR_NAME = "generate-whitebox-v2v-package"
VIDEO_PROMPT_PREFIX = "将无声白模预演替换为真人实拍，"
