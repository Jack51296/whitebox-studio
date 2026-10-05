from __future__ import annotations

import math

import pytest

from wbs.blender import kinematics as K


def test_time_map_identity_and_monotone():
    tm = K.TimeMap.identity(10)
    assert tm.source(3.5) == pytest.approx(3.5)
    slow = K.TimeMap([[0, 0], [4, 4], [6, 4.5], [10, 8.5]], "monotone_cubic")
    values = [slow.source(t / 10) for t in range(101)]
    assert all(b >= a for a, b in zip(values, values[1:]))
    assert slow.edit(slow.source(5.0)) == pytest.approx(5.0, abs=1e-6)
    with pytest.raises(ValueError):
        K.TimeMap([[0, 0], [1, 0]], "linear")


def test_response_is_zero_outside_window_and_reproducible():
    layer = {"clock": "edit", "start": 1.0, "end": 3.0, "translation_m": [0.05, 0.05, 0.05],
             "rotation_deg": [1, 1, 1], "frequency_hz": [1, 3], "seed": 7}
    assert K.sample_response(0.5, 0.5, layer) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    inside = K.sample_response(2.0, 2.0, layer)
    assert inside == K.sample_response(2.0, 2.0, layer)
    assert any(abs(v) > 0 for v in inside[0])
    with pytest.raises(ValueError):
        K.sample_response(2.0, 2.0, {**layer, "frequency_hz": [1, 20]})


def test_cubic_track_has_no_overshoot_and_holds():
    track = K.Track([[0, 0, 0, 0], [1, 1, 0, 0], [2, 1, 0, 0], [3, 3, 0, 0]])
    xs = [track.at(t / 50)[0] for t in range(151)]
    assert min(xs) >= -1e-9 and max(xs) <= 3 + 1e-9
    assert all(abs(track.at(1 + k / 20)[0] - 1.0) < 1e-9 for k in range(21))


def test_heading_holds_while_stationary():
    track = K.Track([[0, 0, 0, 0], [1, 1, 0, 0], [2, 1, 0, 0]], "linear")
    yaws = K.headings(track, [i / 24 for i in range(49)])
    assert yaws[10] == pytest.approx(-90.0, abs=1e-6)
    assert yaws[-1] == pytest.approx(yaws[30], abs=1e-6)


def _scene(shots):
    return {"duration_s": 4.0, "fps": 24, "actors": [], "shots": shots, "blocks": []}


def test_shot_frames_partition_timeline():
    shots = [{"id": "S01", "start_s": 0.0, "end_s": 1.5, "camera": {"keys": [[0, 0, 0, 1]], "aim_keys": [[0, 0, 5, 1]]}},
             {"id": "S02", "start_s": 1.5, "end_s": 4.0, "camera": {"keys": [[0, 0, 0, 1]], "aim_keys": [[0, 0, 5, 1]]}}]
    ev = K.SceneEvaluator(_scene(shots))
    covered = []
    for i in range(2):
        first, last = ev.shot_frames(i)
        covered += list(range(first, last + 1))
        assert all(ev.shot_index(ev.frame_time(f)) == i for f in range(first, last + 1))
    assert covered == list(range(1, ev.frames + 1))


def test_block_aabb_rotated():
    lo, hi = K.block_aabb({"center": [0, 0, 1], "size": [2, 1, 2], "rotation_deg": [0, 0, 90]})
    assert (hi[0] - lo[0], hi[1] - lo[1]) == pytest.approx((1, 2))
    assert K.point_aabb_distance((0, 0, 1), lo, hi) < 0
    assert K.cylinder_box_penetration((0, 0, 2.0), 0.3, 1.7, lo, hi) == 0.0
    assert math.isclose(K.cylinder_box_penetration((0.7, 0, 0), 0.3, 1.7, lo, hi), 0.1, abs_tol=1e-9)
