"""Vision worker: runs inside the isolated .venv-vision interpreter (torch, transformers, DA3 …).

Usage: python worker_main.py <command> <request.json> <response.json>

Standalone on purpose (no wbs imports): the main environment talks to it only through JSON / NPZ
files, so heavy or conflicting dependencies never enter the production interpreter.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import traceback
from pathlib import Path

MODELS = Path(os.environ.get("WBS_MODELS_DIR", Path(__file__).resolve().parents[3] / "models"))


class _NotInstalled:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("该功能依赖 GPL 许可的包（evo / plyfile），按许可证政策未装入视觉工作环境")


def _stub(dotted: str, **attrs) -> None:
    """Placeholder for a module depth_anything_3 imports at load time but inference never calls."""
    import types

    if importlib.util.find_spec(dotted.split(".")[0]) is not None:
        return
    parts = dotted.split(".")
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        module = sys.modules.setdefault(name, types.ModuleType(name))
        module.__path__ = []
    for key, value in attrs.items():
        setattr(sys.modules[dotted], key, value)


def _da3_api():
    """Import DA3 without its GPL-licensed extras (pose alignment to given cameras, PLY export)."""
    _stub("evo.core.trajectory", PosePath3D=_NotInstalled)
    _stub("plyfile", PlyData=_NotInstalled, PlyElement=_NotInstalled)
    from depth_anything_3.api import DepthAnything3

    return DepthAnything3


def _device():
    import torch

    return "cuda" if torch.cuda.is_available() else "cpu"


def _read_frames(video: str, indices: list[int], width: int | None = None) -> dict[int, object]:
    """RGB frames (numpy) for the wanted 0-based indices, decoded sequentially."""
    import cv2

    wanted, out = set(indices), {}
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video}")
    i, last = 0, max(wanted) if wanted else -1
    while i <= last:
        ok, frame = cap.read()
        if not ok:
            break
        if i in wanted:
            if width and frame.shape[1] != width:
                h = int(round(frame.shape[0] * width / frame.shape[1] / 2) * 2)
                frame = cv2.resize(frame, (width, h), interpolation=cv2.INTER_AREA)
            out[i] = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        i += 1
    cap.release()
    return out


def cmd_selftest(_: dict) -> dict:
    import torch

    packages = {name: importlib.util.find_spec(name) is not None
                for name in ("transformers", "depth_anything_3", "mapanything", "sam3", "onnx", "onnxruntime")}
    import_errors = {}
    if packages["depth_anything_3"]:
        try:
            _da3_api()
        except Exception as exc:  # noqa: BLE001
            packages["depth_anything_3"] = False
            import_errors["depth_anything_3"] = f"{type(exc).__name__}: {exc}"
    cuda = torch.cuda.is_available()
    return {"ok": True, "python": sys.executable, "torch": torch.__version__, "cuda": cuda,
            "device": torch.cuda.get_device_name(0) if cuda else "cpu",
            "vram_gb": round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1) if cuda else 0,
            "packages": packages, "import_errors": import_errors}


def cmd_export_transnetv2(req: dict) -> dict:
    import numpy as np
    import torch

    code = MODELS / "transnetv2-code" / "transnetv2_pytorch.py"
    weights = MODELS / "transnetv2" / "transnetv2-pytorch-weights.pth"
    out = MODELS / "transnetv2" / "transnetv2.onnx"
    spec = importlib.util.spec_from_file_location("transnetv2_pytorch", code)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    model = module.TransNetV2()
    model.load_state_dict(torch.load(weights, map_location="cpu"))
    model.eval()

    class Single(torch.nn.Module):
        def __init__(self, net):
            super().__init__()
            self.net = net

        def forward(self, frames):
            single, _ = self.net(frames)
            return torch.sigmoid(single)

    wrapped = Single(model).eval()
    dummy = torch.from_numpy(np.random.default_rng(0).integers(0, 255, (1, 100, 27, 48, 3), dtype=np.uint8))
    with torch.no_grad():
        expected = wrapped(dummy).numpy()
    kwargs = dict(input_names=["frames"], output_names=["single_frame"], opset_version=17)
    try:
        torch.onnx.export(wrapped, (dummy,), str(out), dynamo=False, **kwargs)
    except TypeError:
        torch.onnx.export(wrapped, (dummy,), str(out), **kwargs)
    import onnxruntime as ort

    got = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"]).run(None, {"frames": dummy.numpy()})[0]
    diff = float(np.abs(got - expected).max())
    if diff > 1e-3:
        out.unlink()
        raise RuntimeError(f"ONNX output differs from PyTorch by {diff}")
    return {"onnx": str(out), "max_abs_diff": diff}


def _union(boxes: list[list[float]]) -> list[float]:
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def cmd_detect_subject(req: dict) -> dict:
    """Grounding DINO boxes for a text prompt (+ SAM 2.1 masks from those boxes when ``masks``)."""
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

    device = _device()
    dino_dir = MODELS / "grounding-dino-tiny"
    processor = AutoProcessor.from_pretrained(dino_dir)
    model = AutoModelForZeroShotObjectDetection.from_pretrained(dino_dir).to(device).eval()
    frames = _read_frames(req["video"], req["frames"], req.get("width", 640))
    prompt = req.get("prompt", "person.")
    box_thr, text_thr, keep_ratio = req.get("box_threshold", 0.3), req.get("text_threshold", 0.25), req.get("keep_ratio", 0.6)
    sam = None
    if req.get("masks") and (MODELS / "sam2.1-hiera-tiny" / "model.safetensors").exists():
        from transformers import Sam2Model, Sam2Processor

        sam_dir = MODELS / "sam2.1-hiera-tiny"
        sam = (Sam2Processor.from_pretrained(sam_dir), Sam2Model.from_pretrained(sam_dir).to(device).eval())
    mask_dir = Path(req["mask_dir"]) if req.get("mask_dir") else None
    results, order = {}, sorted(frames)
    for start in range(0, len(order), 8):
        batch = order[start:start + 8]
        images = [Image.fromarray(frames[i]) for i in batch]
        inputs = processor(images=images, text=[prompt] * len(images), return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = model(**inputs)
        sizes = [img.size[::-1] for img in images]
        try:
            post = processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, threshold=box_thr, text_threshold=text_thr, target_sizes=sizes)
        except TypeError:
            post = processor.post_process_grounded_object_detection(
                outputs, inputs.input_ids, box_threshold=box_thr, text_threshold=text_thr, target_sizes=sizes)
        for i, img, det in zip(batch, images, post):
            w, h = img.size
            scores = det["scores"].tolist()
            if not scores:
                continue
            best = max(scores)
            labels = det.get("text_labels", det.get("labels"))
            boxes = [[b[0] / w, b[1] / h, b[2] / w, b[3] / h] for b, s in zip(det["boxes"].tolist(), scores)
                     if s >= keep_ratio * best]
            row = {"boxes": [[round(v, 4) for v in b] for b in boxes], "score": round(best, 4),
                   "label": str(labels[scores.index(best)]) if labels is not None else prompt}
            bbox = _union(boxes)
            area = (bbox[2] - bbox[0]) * (bbox[3] - bbox[1])
            if sam is not None:
                sam_proc, sam_model = sam
                pixel_boxes = [[[b[0] * w, b[1] * h, b[2] * w, b[3] * h] for b in boxes]]
                sin = sam_proc(images=img, input_boxes=pixel_boxes, return_tensors="pt").to(device)
                with torch.no_grad():
                    sout = sam_model(**sin, multimask_output=False)
                masks = sam_proc.post_process_masks(sout.pred_masks.cpu(), sin["original_sizes"])[0]
                mask = masks.reshape(-1, h, w).any(dim=0).numpy()
                if mask.any():
                    ys, xs = np.nonzero(mask)
                    bbox = [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]
                    area = float(mask.mean())
                    row["mask_area"] = round(area, 5)
                    if mask_dir is not None:
                        mask_dir.mkdir(parents=True, exist_ok=True)
                        path = mask_dir / f"mask_f{i + 1:05}.png"
                        Image.fromarray((mask * 255).astype(np.uint8)).save(path)
                        row["mask_file"] = str(path)
            row.update(bbox=[round(float(v), 4) for v in bbox], area=round(float(area), 5),
                       center=[round((bbox[0] + bbox[2]) / 2, 4), round((bbox[1] + bbox[3]) / 2, 4)],
                       components=len(boxes))
            results[str(i)] = row
    return {"frames": results, "backend": "grounding_dino" + ("+sam2.1" if sam else ""), "device": device}


def cmd_sam3_subject(req: dict) -> dict:
    """Adapter for SAM 3 text-prompted segmentation; weights must be supplied by the user (gated repo)."""
    weights = MODELS / "sam3"
    if not (weights / "config.json").exists():
        raise RuntimeError("models/sam3 为空：SAM 3 权重是门控仓库，本平台不申请；获批后放入 models/sam3/ 再启用")
    import numpy as np
    import torch
    from PIL import Image
    from transformers import Sam3Model, Sam3Processor

    device = _device()
    processor = Sam3Processor.from_pretrained(weights)
    model = Sam3Model.from_pretrained(weights).to(device).eval()
    frames = _read_frames(req["video"], req["frames"], req.get("width", 640))
    results = {}
    for i, rgb in sorted(frames.items()):
        img = Image.fromarray(rgb)
        inputs = processor(images=img, text=req.get("prompt", "person"), return_tensors="pt").to(device)
        with torch.no_grad():
            outputs = model(**inputs)
        seg = processor.post_process_instance_segmentation(
            outputs, threshold=req.get("threshold", 0.5), mask_threshold=0.5,
            target_sizes=inputs.get("original_sizes").tolist())[0]
        if len(seg["masks"]) == 0:
            continue
        mask = seg["masks"].any(dim=0).cpu().numpy()
        ys, xs = np.nonzero(mask)
        h, w = mask.shape
        bbox = [xs.min() / w, ys.min() / h, (xs.max() + 1) / w, (ys.max() + 1) / h]
        results[str(i)] = {"bbox": [round(float(v), 4) for v in bbox], "area": round(float(mask.mean()), 5),
                           "center": [round((bbox[0] + bbox[2]) / 2, 4), round((bbox[1] + bbox[3]) / 2, 4)],
                           "score": round(float(seg["scores"].max()), 4), "components": int(len(seg["masks"]))}
    return {"frames": results, "backend": "sam3", "device": device}


def _even(start: int, end: int, count: int) -> list[int]:
    n = end - start + 1
    if n <= count:
        return list(range(start, end + 1))
    return sorted({start + round(k * (n - 1) / (count - 1)) for k in range(count)})


def _save_track(path: Path, rows: list[dict], meta: dict) -> None:
    import numpy as np

    np.savez_compressed(
        path, frames=np.array([r["frame"] for r in rows], dtype=np.int32),
        shot=np.array([r["shot"] for r in rows], dtype=np.int32),
        c2w=np.stack([r["c2w"] for r in rows]).astype(np.float32),
        K=np.stack([r["K"] for r in rows]).astype(np.float32),
        depth=np.stack([r["depth"] for r in rows]).astype(np.float16),
        conf=np.stack([r["conf"] for r in rows]).astype(np.float16),
        meta=np.array(json.dumps(meta)))


def cmd_geometry_da3(req: dict) -> dict:
    import numpy as np
    import torch

    DepthAnything3 = _da3_api()
    name = req.get("model", "da3-small")
    device = _device()
    model = DepthAnything3.from_pretrained(str(MODELS / name)).to(device).eval()
    metric_name = req.get("metric_model")
    metric = None
    if metric_name and (MODELS / metric_name / "model.safetensors").exists():
        metric = DepthAnything3.from_pretrained(str(MODELS / metric_name)).to(device).eval()
    width, height = req["width"], req["height"]
    rows, scales = [], []
    for n, shot in enumerate(req["shots"]):
        idx = _even(shot["start"], shot["end"], req.get("max_views", 24))
        frames = _read_frames(req["video"], idx)
        idx = [i for i in idx if i in frames]
        pred = model.inference([frames[i] for i in idx], process_res=req.get("process_res", 504))
        h, w = pred.depth.shape[1:]
        conf_all = pred.conf if getattr(pred, "conf", None) is not None else np.ones_like(pred.depth)
        scale = 1.0
        if metric is not None:
            ratios = []
            for k in range(0, len(idx), max(1, len(idx) // 6)):
                out = metric.inference([frames[idx[k]]], process_res=req.get("process_res", 504))
                focal = float(np.mean(np.diag(pred.intrinsics[k])[:2]))
                m = focal * out.depth[0] / 300.0
                if m.shape != pred.depth[k].shape:
                    import cv2

                    m = cv2.resize(m, (w, h), interpolation=cv2.INTER_LINEAR)
                valid = (pred.depth[k] > 1e-6) & (conf_all[k] >= np.median(conf_all[k]))
                ratios.append(float(np.median(m[valid] / pred.depth[k][valid])))
            scale = float(np.median(ratios)) if ratios else 1.0
            scales.append(scale)
        for k, i in enumerate(idx):
            w2c = np.eye(4)
            w2c[:3, :4] = pred.extrinsics[k]
            c2w = np.linalg.inv(w2c)
            c2w[:3, 3] *= scale
            K = np.array(pred.intrinsics[k], dtype=float)
            K[0] *= width / w
            K[1] *= height / h
            step = max(1, w // 160)
            rows.append({"frame": i, "shot": n, "c2w": c2w, "K": K,
                         "depth": pred.depth[k][::step, ::step] * scale, "conf": conf_all[k][::step, ::step]})
        if device == "cuda":
            torch.cuda.empty_cache()
    out = Path(req["out"])
    source = f"depth_anything_3:{name}" + (f"+{metric_name}" if metric is not None else "")
    _save_track(out, rows, {"source": source, "metric": metric is not None, "convention": "opencv_c2w",
                            "image_size": [width, height], "metric_scales": scales})
    return {"track": str(out), "frames": len(rows), "model": source, "device": device, "metric_scales": scales}


def cmd_geometry_mapanything(req: dict) -> dict:
    import numpy as np
    import torch
    from mapanything.models import MapAnything

    weights = MODELS / "map-anything-apache"
    if not (weights / "model.safetensors").exists():
        raise RuntimeError("缺少 models/map-anything-apache（python scripts/fetch_models.py --models map-anything-apache）")
    device = _device()
    model = MapAnything.from_pretrained(str(weights)).to(device).eval()
    width, height = req["width"], req["height"]
    rows = []
    for n, shot in enumerate(req["shots"]):
        idx = _even(shot["start"], shot["end"], req.get("max_views", 16))
        frames = _read_frames(req["video"], idx)
        views = [{"img": torch.from_numpy(frames[i]).permute(2, 0, 1)[None].float() / 255.0, "data_norm_type": ["identity"]}
                 for i in idx if i in frames]
        preds = model.infer(views, memory_efficient_inference=True, use_amp=True, amp_dtype="bf16")
        for i, p in zip(idx, preds):
            depth = p["depth_z"][0, ..., 0].float().cpu().numpy()
            h, w = depth.shape
            K = p["intrinsics"][0].float().cpu().numpy().astype(float)
            K[0] *= width / w
            K[1] *= height / h
            step = max(1, w // 160)
            conf = p.get("conf")
            conf = conf[0].float().cpu().numpy() if conf is not None else np.ones_like(depth)
            rows.append({"frame": i, "shot": n, "c2w": p["camera_poses"][0].float().cpu().numpy(), "K": K,
                         "depth": depth[::step, ::step], "conf": conf.reshape(depth.shape)[::step, ::step]})
    out = Path(req["out"])
    _save_track(out, rows, {"source": "mapanything:map-anything-apache", "metric": True, "convention": "opencv_c2w",
                            "image_size": [width, height]})
    return {"track": str(out), "frames": len(rows), "device": device}


COMMANDS = {"selftest": cmd_selftest, "export_transnetv2": cmd_export_transnetv2, "detect_subject": cmd_detect_subject,
            "sam3_subject": cmd_sam3_subject, "geometry_da3": cmd_geometry_da3,
            "geometry_mapanything": cmd_geometry_mapanything}


def main() -> None:
    command, req_path, resp_path = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    started = time.time()
    try:
        request = json.loads(req_path.read_text(encoding="utf-8"))
        result = COMMANDS[command](request)
    except Exception as exc:  # noqa: BLE001 - reported to the caller, which falls back
        result = {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()[-2000:]}
    result["seconds"] = round(time.time() - started, 2)
    resp_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
