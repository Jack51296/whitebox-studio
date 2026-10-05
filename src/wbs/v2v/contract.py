"""SP输出_v3.json validator — every rule of references/output-schema.md that can be checked mechanically."""

from __future__ import annotations

import re
from typing import Any

from . import VIDEO_PROMPT_PREFIX

TOP = {"status", "image_prompts", "video_prompt", "issues"}
IMAGE_FIELDS = {"id", "ref", "scene_ids", "name", "purpose", "shot_ids", "image_type", "generation_mode", "space_source",
                "appearance_sources", "background_constraints", "state_scope", "video_usage", "prompt_constraints",
                "prompt", "reference_image_ids"}
ANCHOR_FIELDS = {"id", "shot_id", "time_s", "time_precision", "role", "description", "frame_number", "frame_number_base"}
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
SHOT_LINE = re.compile(r"镜头\s*(\d+)\s*[｜|]")
IMAGE_REF = re.compile(r"@图(\d+)")


def _str_list(value: Any, nonempty: bool = True) -> bool:
    return isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value) and (bool(value) or not nonempty)


def _pos_int_list(value: Any) -> bool:
    return (isinstance(value, list) and bool(value) and all(type(v) is int and v > 0 for v in value)
            and len(set(value)) == len(value))


def video_shot_refs(video_prompt: str) -> dict[int, set[int]]:
    """Map shot number → set of @图N numbers referenced in that shot's block of the video prompt."""
    refs: dict[int, set[int]] = {}
    matches = list(SHOT_LINE.finditer(video_prompt))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(video_prompt)
        block = video_prompt[m.start():end]
        refs.setdefault(int(m.group(1)), set()).update(int(n) for n in IMAGE_REF.findall(block))
    return refs


def validate(plan: Any, shot_count: int | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
    if set(plan) != TOP:
        errors.append(f"top-level fields must be exactly {sorted(TOP)}; got {sorted(plan)}")
    status = plan.get("status")
    if status not in ("complete", "partial", "needs_input"):
        errors.append("status must be complete | partial | needs_input")
    images = plan.get("image_prompts")
    video = plan.get("video_prompt")
    if not isinstance(images, list):
        return errors + ["image_prompts must be an array"]
    if not isinstance(video, str):
        errors.append("video_prompt must be a string")
        video = ""
    issues = plan.get("issues")
    if not isinstance(issues, list):
        errors.append("issues must be an array")
    else:
        for n, issue in enumerate(issues):
            ok = (isinstance(issue, dict) and set(issue) == {"type", "shot_ids", "description", "handling"}
                  and all(isinstance(issue.get(k), str) for k in ("type", "description", "handling"))
                  and isinstance(issue.get("shot_ids"), list) and all(type(v) is int for v in issue["shot_ids"]))
            if not ok:
                errors.append(f"issues[{n}] must be {{type, shot_ids:int[], description, handling}}")
    if status == "needs_input":
        if images or video:
            errors.append("needs_input requires empty image_prompts and empty video_prompt")
        return errors
    if not video.startswith(VIDEO_PROMPT_PREFIX):
        errors.append(f"video_prompt must start with '{VIDEO_PROMPT_PREFIX}'")
    shot_refs = video_shot_refs(video)
    if shot_count is not None:
        missing = [n for n in range(1, shot_count + 1) if n not in shot_refs]
        if missing:
            errors.append(f"video_prompt has no '镜头N｜' block for shots {missing}")

    ids: list[str] = []
    anchor_seen: dict[str, tuple] = {}
    for i, item in enumerate(images, 1):
        where = f"image_prompts[{i - 1}]"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object")
            continue
        if set(item) != IMAGE_FIELDS:
            errors.append(f"{where}: fields must be the 16 contract fields; missing {sorted(IMAGE_FIELDS - set(item))}, "
                          f"extra {sorted(set(item) - IMAGE_FIELDS)}")
            continue
        iid = item["id"]
        if not isinstance(iid, str) or not SAFE_ID.match(iid):
            errors.append(f"{where}: id must use letters, digits, '_' or '-'")
        elif iid in ids:
            errors.append(f"{where}: duplicate id {iid}")
        if item["ref"] != f"@图{i}":
            errors.append(f"{where}: ref must be @图{i} (consecutive from @图1)")
        if not _str_list(item["scene_ids"]):
            errors.append(f"{where}: scene_ids must be a non-empty string array")
        for key in ("name", "purpose", "prompt"):
            if not isinstance(item[key], str) or not item[key].strip():
                errors.append(f"{where}: {key} must be a non-empty string")
        if not _pos_int_list(item["shot_ids"]):
            errors.append(f"{where}: shot_ids must be unique positive integers")
            shot_ids: set[int] = set()
        else:
            shot_ids = set(item["shot_ids"])
            if shot_count is not None and max(shot_ids) > shot_count:
                errors.append(f"{where}: shot_ids exceed the {shot_count} existing shots")
        if item["image_type"] not in ("scene_overview", "shot_scene", "subject_asset"):
            errors.append(f"{where}: invalid image_type")
        mode = item["generation_mode"]
        if mode not in ("text_to_image", "image_to_image"):
            errors.append(f"{where}: generation_mode must be text_to_image | image_to_image")
        space = item["space_source"]
        anchors: list = []
        if not isinstance(space, dict) or set(space) != {"kind", "anchors", "preserve", "design_freedom"}:
            errors.append(f"{where}: space_source must be {{kind, anchors, preserve, design_freedom}}")
        else:
            anchors = space["anchors"] if isinstance(space["anchors"], list) else []
            if space["kind"] not in ("scene_description", "shot_frame", "neutral_background"):
                errors.append(f"{where}: invalid space_source.kind")
            if not _str_list(space["preserve"]) or not _str_list(space["design_freedom"]):
                errors.append(f"{where}: preserve and design_freedom must be non-empty string arrays")
            roles = []
            for a in anchors:
                if not isinstance(a, dict) or set(a) != ANCHOR_FIELDS:
                    errors.append(f"{where}: every anchor needs exactly the 8 anchor fields")
                    continue
                roles.append(a["role"])
                if not isinstance(a["id"], str) or not SAFE_ID.match(a["id"]):
                    errors.append(f"{where}: anchor id {a['id']!r} not a safe identifier")
                if type(a["shot_id"]) is not int or a["shot_id"] <= 0:
                    errors.append(f"{where}: anchor shot_id must be a positive integer")
                if a["time_s"] is not None and (not isinstance(a["time_s"], (int, float)) or a["time_s"] < 0):
                    errors.append(f"{where}: anchor time_s must be non-negative or null")
                if a["time_precision"] not in ("exact", "approximate"):
                    errors.append(f"{where}: anchor time_precision must be exact | approximate")
                if a["role"] not in ("primary", "context"):
                    errors.append(f"{where}: anchor role must be primary | context")
                if a["frame_number"] is not None and (type(a["frame_number"]) is not int or a["frame_number"] < 0):
                    errors.append(f"{where}: anchor frame_number must be a non-negative integer or null")
                if a["frame_number_base"] not in (0, 1, None):
                    errors.append(f"{where}: anchor frame_number_base must be 0, 1 or null")
                if a["frame_number"] is not None and a["frame_number_base"] is None:
                    errors.append(f"{where}: anchor frame_number needs frame_number_base")
                if a["frame_number"] is None and a["time_s"] is None:
                    errors.append(f"{where}: anchor needs time_s or frame_number")
                if a["role"] == "primary" and shot_ids and a["shot_id"] not in shot_ids:
                    errors.append(f"{where}: primary anchor shot {a['shot_id']} not in shot_ids")
                locate = (a["shot_id"], a["time_s"], a["frame_number"], a["frame_number_base"])
                if a["id"] in anchor_seen and anchor_seen[a["id"]] != locate:
                    errors.append(f"{where}: anchor id {a['id']} reused with different locating information")
                anchor_seen.setdefault(a["id"], locate)
            if roles != sorted(roles, key=lambda r: r != "primary"):
                errors.append(f"{where}: anchors must list primary before context")
            if space["kind"] == "shot_frame" and (roles.count("primary") != 1 or (roles and roles[0] != "primary")):
                errors.append(f"{where}: shot_frame needs exactly one primary anchor, first")
        deps = item["appearance_sources"]
        dep_ids = []
        if not isinstance(deps, list):
            errors.append(f"{where}: appearance_sources must be an array")
            deps = []
        for d in deps:
            if not isinstance(d, dict) or set(d) != {"image_id", "inherit", "exclude"} or not _str_list(d.get("inherit")) \
                    or not _str_list(d.get("exclude")):
                errors.append(f"{where}: appearance source must be {{image_id, inherit[], exclude[]}} (non-empty arrays)")
                continue
            dep_ids.append(d["image_id"])
            if d["image_id"] not in ids:
                errors.append(f"{where}: appearance source {d['image_id']} must be an earlier image of this task")
        if item["reference_image_ids"] != dep_ids or len(set(dep_ids)) != len(dep_ids):
            errors.append(f"{where}: reference_image_ids must equal appearance_sources image_ids, same order, no duplicates")
        if mode == "text_to_image" and (anchors or deps or item["reference_image_ids"]):
            errors.append(f"{where}: text_to_image must have empty anchors, appearance_sources and reference_image_ids")
        if mode == "image_to_image" and not anchors and not deps:
            errors.append(f"{where}: image_to_image must pass at least one real image")
        bg = item["background_constraints"]
        if not isinstance(bg, dict) or set(bg) != {"required", "forbidden"} or not _str_list(bg["required"]) \
                or not _str_list(bg["forbidden"], nonempty=False):
            errors.append(f"{where}: background_constraints must be {{required: non-empty[], forbidden: []}}")
        st = item["state_scope"]
        if not isinstance(st, dict) or set(st) != {"shown", "video_rule"} or not all(
                isinstance(st.get(k), str) and st[k].strip() for k in ("shown", "video_rule")):
            errors.append(f"{where}: state_scope must be {{shown, video_rule}} non-empty strings")
        usage = item["video_usage"]
        usage_shots = set()
        if not isinstance(usage, list) or not usage:
            errors.append(f"{where}: video_usage must be a non-empty array")
        else:
            for u in usage:
                if not isinstance(u, dict) or set(u) != {"shot_id", "inherit", "exclude"} or type(u.get("shot_id")) is not int \
                        or u["shot_id"] <= 0 or not _str_list(u.get("inherit")) or not _str_list(u.get("exclude")):
                    errors.append(f"{where}: video_usage items must be {{shot_id>0, inherit[], exclude[]}} (non-empty)")
                    continue
                usage_shots.add(u["shot_id"])
        if not _str_list(item["prompt_constraints"]):
            errors.append(f"{where}: prompt_constraints must be a non-empty string array")
        text_shots = {shot for shot, refs in shot_refs.items() if i in refs}
        if shot_ids and (usage_shots != shot_ids or text_shots != shot_ids):
            errors.append(f"{where}: shot_ids {sorted(shot_ids)}, video_usage {sorted(usage_shots)} and video_prompt "
                          f"references {sorted(text_shots)} must be the same set")
        if isinstance(iid, str):
            ids.append(iid)
    return errors
