from __future__ import annotations

import math

import numpy as np

from wbs.reverse.geometry import ground_plane, interpolate_cameras, load_track, solve_geometry

W, H, FPS, N = 640, 360, 24, 48
K = np.array([[500.0, 0, W / 2], [0, 500.0, H / 2], [0, 0, 1]])
R_CAM = np.array([[1.0, 0, 0], [0, 0, 1], [0, -1, 0]])  # OpenCV axes of a level camera facing +Y in a Z-up world


def _truth(f: int) -> tuple[np.ndarray, np.ndarray]:
    camera = np.array([f / FPS * 1.5, 0.0, 1.6])
    subject = np.array([f / FPS * 1.5, 8.0, 0.0])
    return camera, subject


def _depth(camera: np.ndarray, h: int = 36, w: int = 64) -> np.ndarray:
    v, u = np.mgrid[0:h, 0:w]
    rays = np.stack([((u + 0.5) * W / w - K[0, 2]) / K[0, 0], ((v + 0.5) * H / h - K[1, 2]) / K[1, 1], np.ones((h, w))], -1)
    world = rays @ R_CAM.T
    t_ground = np.where(world[..., 2] < -1e-6, -camera[2] / np.minimum(world[..., 2], -1e-6), np.inf)
    t_wall = np.where(world[..., 1] > 1e-6, (20.0 - camera[1]) / np.maximum(world[..., 1], 1e-6), np.inf)
    return np.minimum(t_ground, t_wall)


def _box(camera: np.ndarray, subject: np.ndarray) -> list[float]:
    def project(p):
        c = R_CAM.T @ (p - camera)
        return K[0, 0] * c[0] / c[2] + K[0, 2], K[1, 1] * c[1] / c[2] + K[1, 2]

    (uf, vf), (_, vh) = project(subject), project(subject + [0, 0, 1.7])
    half = 0.35 * K[0, 0] / 8.0
    return [(uf - half) / W, vh / H, (uf + half) / W, vf / H]


def _synthetic(sim_scale: float = 0.4) -> tuple[dict, dict]:
    a = math.radians(37)
    q = np.array([[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]) @ \
        np.array([[math.cos(1.1), -math.sin(1.1), 0], [math.sin(1.1), math.cos(1.1), 0], [0, 0, 1]])
    frames = np.arange(0, N, 2)
    c2w, depth, samples = [], [], {}
    for f in frames:
        camera, subject = _truth(int(f))
        m = np.eye(4)
        m[:3, :3] = q @ R_CAM
        m[:3, 3] = sim_scale * (q @ camera) + np.array([3.0, -2.0, 5.0])
        c2w.append(m)
        depth.append(sim_scale * _depth(camera))
    for f in range(2, N, 6):
        samples[str(f)] = {"bbox": _box(*_truth(f)), "area": 0.01, "center": [0.5, 0.5]}
    track = {"frames": frames.astype(np.int32), "shot": np.zeros(len(frames), np.int32), "c2w": np.array(c2w),
             "K": np.repeat(K[None], len(frames), 0), "depth": np.array(depth, np.float32),
             "conf": np.ones((len(frames), 36, 64), np.float32),
             "meta": {"source": "synthetic", "metric": False, "image_size": [W, H]}}
    flow = {"zoom_total": 1.0, "pan_x_total": 0.0, "pan_y_total": 0.0, "roll_total_deg": 0.0, "per_frame": []}
    analysis = {"video": {"fps": FPS, "width": W, "height": H, "frames": N, "file": "synthetic.mp4"},
                "shots": [{"id": "S01", "start_frame": 0, "end_frame": N - 1, "occupancy_samples": samples, "flow": flow}]}
    return analysis, track


def test_geometry_recovers_gravity_scale_and_truck():
    analysis, track = _synthetic()
    scene, info = solve_geometry(analysis, track, "syn", "synthetic", camera_height=1.6)
    shot = info["shots"][0]
    assert shot["gravity"] == "ground_plane" and shot["scale_from"].startswith("camera_height")
    keys = scene.shots[0].camera.keys
    assert all(abs(k[3] - 1.6) < 0.05 for k in keys)
    travelled = math.dist(keys[0][1:3], keys[-1][1:3])
    assert abs(travelled - (N - 1) / FPS * 1.5) < 0.1
    assert abs(keys[-1][2] - keys[0][2]) < 0.05, "a truck must stay on the camera's lateral axis (no pan↔truck mix-up)"
    subject = scene.actors[0].path.keys
    assert all(abs(k[3]) < 1e-6 for k in subject)
    ahead = [math.dist(k[1:3], keys[0][1:3]) for k in subject[:1]]
    assert 7.0 < ahead[0] < 9.5
    walls = [b for b in scene.blocks if b.role == "depth_occupancy"]
    assert walls and all(abs(b.center[1] - keys[0][2] - 20.0) < 1.0 for b in walls), "wall stands 20 m ahead of the camera"
    assert scene.shots[0].lens_mm == round(500.0 / W * 36.0, 2)


def test_geometry_keeps_blocks_out_of_the_camera_path():
    analysis, track = _synthetic()
    scene, _ = solve_geometry(analysis, track, "syn", "synthetic")
    cams = np.array([k[1:] for k in scene.shots[0].camera.keys])
    for b in scene.blocks:
        if b.role != "depth_occupancy":
            continue
        assert np.min(np.abs(cams[:, 1] - b.center[1])) > b.size[1] / 2


def test_ground_plane_and_interpolation():
    rng = np.random.default_rng(0)
    pts = np.c_[rng.uniform(-5, 5, (500, 2)), rng.normal(0, 0.01, 500)]
    n, p, ratio = ground_plane(np.r_[pts, rng.uniform(-5, 5, (50, 3))], np.array([0, 0.2, 1.0]), 0.05)
    assert n[2] > 0.99 and ratio > 0.85
    c2w = np.repeat(np.eye(4)[None], 2, 0)
    c2w[1, :3, 3] = [2, 0, 0]
    mid = interpolate_cameras(np.array([0, 10]), c2w, [5])[5]
    assert np.allclose(mid[:3, 3], [1, 0, 0])


def test_load_megasam_npz(tmp_path):
    analysis, track = _synthetic()
    path = tmp_path / "sgd_cvd_hr.npz"
    np.savez(path, cam_c2w=track["c2w"], intrinsic=K / 10.0 * np.array([[1], [1], [10]]), depths=track["depth"])
    loaded = load_track(path, analysis, stride=2)
    assert loaded["meta"]["source"] == "megasam" and list(loaded["frames"][:3]) == [0, 2, 4]
    assert (loaded["shot"] == 0).all() and loaded["meta"]["image_size"] == [64, 36]
