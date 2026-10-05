from __future__ import annotations

import pytest
from pydantic import ValidationError

from wbs.config import QC
from wbs.models.scene import SceneSpec
from wbs.qc.dynamic import framing_precheck
from wbs.qc.visibility import setup_readability

WALL = {"id": "WALL", "shape": "box", "center": [0, 5, 2], "size": [12, 0.5, 4], "role": "wall"}


def _scene(blocks=(), events=(), cam=(0, -12, 1.6)):
    return {"id": "v", "title": "v", "fps": 24, "duration_s": 3.0, "resolution": [320, 180], "blocks": list(blocks),
            "actors": [{"id": "A", "kind": "pawn", "height_m": 1.7, "radius_m": 0.35,
                        "path": {"keys": [[0, -1, 10, 0], [3, 1, 10, 0]], "interpolation": "linear"}}],
            "shots": [{"id": "S01", "start_s": 0.0, "end_s": 3.0, "lens_mm": 35,
                       "camera": {"keys": [[0, *cam]], "aim_actor": "A"}}],
            "events": list(events)}


def test_framing_precheck_counts_a_subject_hidden_behind_a_wall_as_invisible():
    clear = framing_precheck(_scene())
    assert clear["status"] == "passed" and clear["shots"][0]["occluded_frames"] == 0
    hidden = framing_precheck(_scene([WALL]))
    assert hidden["status"] == "failed" and hidden["shots"][0]["occluded_frames"] > 0
    above = framing_precheck(_scene([WALL], cam=(0, -12, 20)))
    assert above["status"] == "passed"


def test_framing_precheck_follows_the_shot_subject():
    scene = _scene()
    scene["actors"].append({"id": "B", "kind": "pawn", "height_m": 1.7, "radius_m": 0.35,
                            "path": {"keys": [[0, 60, -40, 0], [3, 62, -40, 0]], "interpolation": "linear"}})
    scene["shots"][0]["camera"] = {"keys": [[0, 60, -52, 1.6]], "aim_actor": "B"}
    result = framing_precheck(scene)
    assert result["status"] == "passed" and result["shots"][0]["subject"] == "B"
    scene["shots"][0]["camera"] = {"keys": [[0, 60, -52, 1.6]], "aim_keys": [[0, 60, -40, 1.0]]}
    assert framing_precheck(scene)["status"] == "failed", "without an aim actor the first actor is the subject"


def _setup(**extra):
    return {"id": "E01", "at_s": 1.0, "choice": "看见", "consequence": "记住", "role": "setup", **extra}


def test_setup_readability_needs_every_target_readable():
    door = {"id": "DOOR", "shape": "box", "center": [3, 11, 1.2], "size": [1.2, 0.3, 2.4], "role": "door"}
    far = {"id": "FAR", "shape": "box", "center": [40, 300, 1], "size": [1, 1, 2], "role": "sign"}
    qc = QC()
    ok = setup_readability(_scene([door], [_setup(targets=["DOOR"])]), qc)
    assert ok[0]["status"] == "ok" and ok[0]["targets"][0]["width"] >= qc.readability.setup_min_width_fraction
    tiny = setup_readability(_scene([door, far], [_setup(targets=["DOOR", "FAR"])]), qc)
    assert tiny[0]["status"] == "warning" and "FAR" in tiny[0]["detail"]
    hidden = setup_readability(_scene([WALL], [_setup(focus_region=[0, 9, 1, 3, 1, 2])]), qc)
    assert hidden[0]["status"] == "warning" and "focus_region" in hidden[0]["detail"]
    assert setup_readability(_scene(events=[_setup()]), qc)[0]["status"] == "warning"


def test_event_targets_must_exist():
    SceneSpec.model_validate(_scene([WALL], [_setup(targets=["WALL", "A"])]))
    with pytest.raises(ValidationError, match="not block, block group or actor ids"):
        SceneSpec.model_validate(_scene(events=[_setup(targets=["NOPE"])]))
