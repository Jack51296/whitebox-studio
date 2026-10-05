from __future__ import annotations

import math

import numpy as np

from wbs.reverse.benchmark import cut_counts, f1, iou, recommend, umeyama


def test_cut_matching_with_one_frame_tolerance():
    counts = cut_counts([10, 20, 31], [10, 21, 40])
    assert counts == {"tp": 2, "fp": 1, "fn": 1} and f1(counts) == round(2 * 2 / 6, 4)
    assert f1(cut_counts([], [])) == 1.0


def test_umeyama_recovers_similarity():
    rng = np.random.default_rng(3)
    src = rng.normal(size=(40, 3))
    a = math.radians(30)
    r = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
    dst = 2.5 * src @ r.T + np.array([1.0, -2.0, 0.5])
    s, rot, t = umeyama(src, dst)
    assert abs(s - 2.5) < 1e-9 and np.allclose(rot, r) and np.allclose(t, [1.0, -2.0, 0.5])


def test_iou_and_recommendation_rules():
    assert iou([0, 0, 1, 1], [0, 0, 1, 1]) == 1.0 and iou([0, 0, 0.4, 0.4], [0.5, 0.5, 1, 1]) == 0.0
    rec = recommend({"cuts": {"builtin": {"f1": 1.0, "used": ["builtin"]}, "transnetv2": {"f1": 0.7, "used": ["transnetv2"]}},
                     "subject": {"motion": {"iou": 0.2}, "grounding_dino": {"iou": 0.6, "used": ["grounding_dino"]}},
                     "camera": {"heuristic": {"ate_sim3_m": 2.7}, "da3/camera_height": {"ate_sim3_m": 2.4}},
                     "camera_by_move": {"heuristic": {"push": {"ate_sim3_m": 3.0}, "truck": {"ate_sim3_m": 1.0}},
                                        "da3/camera_height": {"push": {"ate_sim3_m": 0.5}, "truck": {"ate_sim3_m": 4.0}}}})
    assert rec["cuts"].startswith("保持 builtin") and "grounding_dino" in rec["subject"]
    assert rec["geometry"].startswith("保持 heuristic") and "['push']" in rec["geometry_by_move"] and "truck" in rec["geometry_by_move"]
