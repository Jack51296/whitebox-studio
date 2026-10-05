from __future__ import annotations

import math
import re

import numpy as np
import pytest

from wbs import vision
from wbs.errors import WbsError
from wbs.reverse.camera_motion import flow_labels, geometry_labels
from wbs.vision import registry
from wbs.vision.fetch import fetch, file_url, verify
from wbs.vision.transnet import cuts_from_predictions


@pytest.fixture
def no_extras(monkeypatch, tmp_path):
    """Pretend nothing optional is installed: empty models dir, no worker, no optional packages."""
    monkeypatch.setenv("WBS_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.setattr("wbs.vision.worker.vision_python", lambda: None)
    monkeypatch.setattr(vision, "has_package", lambda name: False)


def test_registry_pins_every_downloadable_file():
    names = [m.name for m in registry.MODELS]
    assert len(names) == len(set(names))
    for m in registry.MODELS:
        if m.gated:
            assert not m.files and not m.profiles and m.note
            continue
        assert m.commercial and m.files and re.fullmatch(r"[0-9a-f]{40}", m.revision)
        assert all(re.fullmatch(r"[0-9a-f]{64}", f.sha256) and f.size > 0 for f in m.files), m.name
    assert {m.name for m in registry.profile("default")} <= {m.name for m in registry.profile("full")}
    assert any("NC" in why for _, why in registry.EXCLUDED)


def test_fetch_refuses_gated_and_plans_mirror_urls(tmp_path):
    assert fetch(registry.get("sam3"), tmp_path)["status"] == "skipped_gated"
    plan = fetch(registry.get("da3-small"), tmp_path, endpoint="modelscope", dry_run=True)
    assert plan["status"] == "planned" and all(u.startswith("https://modelscope.cn/models/depth-anything/DA3-SMALL/")
                                               for u in plan["urls"])
    spec = registry.get("transnetv2-code")
    assert file_url(spec, spec.files[0]).startswith("https://raw.githubusercontent.com/soCzech/TransNetV2/85cef72")


def test_verify_detects_tampering(tmp_path):
    payload = b"hello model"
    import hashlib

    spec = registry.ModelSpec("toy", "url", "https://example.invalid", "0" * 40, "MIT", "test",
                              (registry.ModelFile("toy.bin", hashlib.sha256(payload).hexdigest(), len(payload)),))
    (tmp_path / "toy").mkdir()
    (tmp_path / "toy" / "toy.bin").write_bytes(payload)
    assert verify(spec, tmp_path)["ok"]
    (tmp_path / "toy" / "toy.bin").write_bytes(b"hello modeL")
    assert verify(spec, tmp_path)["files"][0]["status"] == "sha256_mismatch"


def test_resolve_falls_back_to_builtin_with_reasons(no_extras):
    choice = vision.resolve("cuts", "transnetv2")
    assert choice.used == "builtin" and set(choice.skipped) == {"transnetv2", "pyscenedetect"}
    choice = vision.resolve("subject", "sam3")
    assert choice.used == "motion" and "门控" in choice.skipped["sam3"]
    choice = vision.resolve("geometry", "da3")
    assert choice.used == "heuristic" and choice.fell_back
    assert vision.resolve("cuts").used == "builtin"
    assert vision.resolve("cuts", "nonsense").used == "builtin"


def test_find_cuts_reports_fallback(no_extras):
    from wbs.reverse.cuts import find_cuts

    grays = np.zeros((48, 18, 32), np.uint8)
    grays[24:] = 200
    cuts, _, info = find_cuts(None, grays, 24, "transnetv2")
    assert cuts == [24] and info["backend"] == "builtin" and "transnetv2" in info["fallback_reasons"]


def test_subject_backend_maps_worker_boxes_and_falls_back(monkeypatch, tmp_path):
    from wbs.reverse import subject
    from wbs.vision.worker import VisionUnavailable

    grays = np.zeros((40, 36, 64), np.uint8)
    ranges = [(0, 19), (20, 39)]
    monkeypatch.setattr(vision, "unavailable_reason", lambda kind, name: None)
    calls = []

    def fake_run(command, request, timeout=None):
        calls.append((command, request))
        box = {"bbox": [0.4, 0.3, 0.6, 0.9], "area": 0.12, "center": [0.5, 0.6], "components": 1}
        return {"frames": {str(f): box for f in request["frames"] if f % 2}, "backend": "grounding_dino+sam2.1"}

    monkeypatch.setattr("wbs.vision.worker.run", fake_run)
    occ, info = subject.shot_samples(tmp_path / "v.mp4", grays, ranges, 6, backend="grounding_dino", subject="bird")
    assert info["backend"] == "grounding_dino" and calls[0][0] == "detect_subject"
    assert calls[0][1]["prompt"] == "bird." and len(calls) == 1
    assert all(0 <= f <= 19 for f in occ[0]) and all(20 <= f <= 39 for f in occ[1]) and all(f % 2 for f in occ[1])

    def broken(command, request, timeout=None):
        raise VisionUnavailable("no GPU")

    monkeypatch.setattr("wbs.vision.worker.run", broken)
    occ, info = subject.shot_samples(tmp_path / "v.mp4", grays, ranges, 6, backend="grounding_dino")
    assert info["backend"] == "motion" and "no GPU" in info["error"] and len(occ) == 2


def test_flow_masks_subject_by_segmentation():
    from wbs.reverse.flow import mask_from_segmentation

    seg = np.zeros((10, 20), np.uint8)
    seg[2:5, 5:10] = 255
    mask = mask_from_segmentation((40, 80), seg, grow_px=0)
    assert mask[12, 30] == 0 and mask[0, 0] == 255 and mask.shape == (40, 80)


def test_centerface_joins_yunet_or_reports_why_not(monkeypatch, tmp_path):
    from wbs.privacy import FaceDetector
    from wbs.vision.centerface import onnx_path

    if onnx_path().exists():
        from wbs.vision.centerface import CenterFace

        assert CenterFace().detect(np.zeros((720, 1280, 3), np.uint8)) == []
        detector = FaceDetector(1280, 720, extra=["centerface"])
        assert "centerface" in detector.method and not detector.skipped
        assert detector.detect(np.full((720, 1280, 3), 128, np.uint8)) == []
    monkeypatch.setenv("WBS_MODELS_DIR", str(tmp_path / "empty"))
    try:
        detector = FaceDetector(640, 360, extra=["centerface"])
    except WbsError as exc:
        assert "人脸检测模型" in str(exc), "fresh machine: no YuNet (tools/ not in git), no Haar data, no CenterFace"
        return
    assert "centerface" in detector.skipped and "centerface" not in detector.method


def test_transnet_cut_is_first_frame_after_transition():
    pred = np.array([0, 0, 0.9, 0, 0, 0.7, 0.8, 0, 0])
    assert cuts_from_predictions(pred) == [3, 7]


def _yaw(deg: float) -> np.ndarray:
    a = math.radians(deg)
    return np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])


def _track(rot_end: np.ndarray, move_end: tuple[float, float, float]) -> tuple[np.ndarray, np.ndarray]:
    c2w = np.repeat(np.eye(4)[None], 2, 0)
    c2w[1, :3, :3] = rot_end
    c2w[1, :3, 3] = move_end
    return c2w, np.repeat(np.diag([1000.0, 1000.0, 1.0])[None], 2, 0)


def test_geometry_labels_separate_pan_truck_and_arc():
    assert geometry_labels(*_track(_yaw(20), (0, 0, 0)), depth_median=10)["labels"] == ["pan_right"]
    assert geometry_labels(*_track(np.eye(3), (2, 0, 0)), depth_median=10)["labels"] == ["truck_right"]
    arc = geometry_labels(*_track(_yaw(-25), (3, 0, 0)), depth_median=10)["labels"]
    assert {"pan_left", "truck_right", "arc_right"} <= set(arc)
    assert geometry_labels(*_track(np.eye(3), (0, 0, 2)), depth_median=10)["labels"] == ["dolly_in"]


def test_label_shots_llm_mock_keeps_rules_and_geometry_overrides(workspace, tmp_path):
    from wbs.config import load_settings
    from wbs.ledger import Ledger
    from wbs.providers import CallContext, get_providers
    from wbs.reverse.camera_motion import label_shots

    flow = {"pan_x_total": -0.2, "pan_y_total": 0.0, "zoom_total": 1.0, "roll_total_deg": 0.0, "per_frame": []}
    analysis = {"shots": [{"id": "S01", "start_s": 0, "end_s": 2, "flow": flow, "frame_files": {}},
                          {"id": "S02", "start_s": 2, "end_s": 4, "flow": {**flow, "pan_x_total": 0.0}, "frame_files": {}}]}
    providers = get_providers(load_settings(), Ledger(tmp_path / "l.sqlite"))
    info = label_shots(analysis, tmp_path, providers, CallContext("t/cm", "camera_motion"), backend="llm")
    assert info["backend"] == "llm" and info["simulated"] is True
    assert analysis["shots"][0]["camera_motion"]["labels"] == ["pan_right"]
    assert analysis["shots"][0]["camera_motion"]["labels_zh"] == ["右摇"]
    c2w, K = _track(np.eye(3), (2, 0, 0))
    track = {"shot": np.array([0, 0]), "c2w": c2w, "K": K, "depth": np.full((2, 4, 4), 10.0)}
    label_shots(analysis, tmp_path, None, CallContext("t/cm", "camera_motion"), backend="rules", track=track)
    assert analysis["shots"][0]["camera_motion"]["labels"] == ["truck_right"]
    assert analysis["shots"][0]["camera_motion"]["basis"] == "geometry"


def test_flow_labels_mark_2d_ambiguity():
    flow = {"pan_x_total": 0.3, "pan_y_total": 0.0, "zoom_total": 1.0, "roll_total_deg": 0.0, "per_frame": []}
    out = flow_labels(flow)
    assert out["labels"] == ["pan_left"] and out["ambiguous"] and out["basis"] == "flow"
    still = flow_labels({**flow, "pan_x_total": 0.0})
    assert still["labels"] == ["static"] and not still["ambiguous"]
