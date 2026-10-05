"""Pillow-based boards: labelled contact sheets, 2×2 white-model templates and top-down route maps."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simsun.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).exists():
            try:
                return ImageFont.truetype(candidate, size)
            except OSError:
                continue
    return ImageFont.load_default()


def contact_sheet(cards: list[tuple[Path, str]], out: Path, cols: int = 3, cell: tuple[int, int] = (480, 270),
                  background: str = "#fafafa") -> Path:
    if not cards:
        raise ValueError("contact sheet needs at least one image")
    rows = math.ceil(len(cards) / cols)
    label_h = 34
    sheet = Image.new("RGB", (cell[0] * cols, (cell[1] + label_h) * rows), background)
    draw = ImageDraw.Draw(sheet)
    label_font = font(17)
    for i, (path, label) in enumerate(cards):
        x, y = (i % cols) * cell[0], (i // cols) * (cell[1] + label_h)
        with Image.open(path) as img:
            sheet.paste(img.convert("RGB").resize(cell), (x, y))
        draw.text((x + 8, y + cell[1] + 6), label, fill="#111111", font=label_font)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=92)
    return out


def grid_2x2(images: list[Path], out: Path, size: tuple[int, int] = (1672, 941), gap: int = 8) -> Path:
    """Four-panel white-model template (the '四宫格白模样板图' used by online conversion, [D2])."""
    if len(images) != 4:
        raise ValueError("grid_2x2 needs exactly four images")
    cell = ((size[0] - gap) // 2, (size[1] - gap) // 2)
    sheet = Image.new("RGB", size, "white")
    for i, path in enumerate(images):
        with Image.open(path) as img:
            sheet.paste(img.convert("RGB").resize(cell), ((i % 2) * (cell[0] + gap), (i // 2) * (cell[1] + gap)))
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out, quality=94)
    return out


def _rotated_rect(cx: float, cy: float, sx: float, sy: float, yaw_deg: float) -> list[tuple[float, float]]:
    c, s = math.cos(math.radians(yaw_deg)), math.sin(math.radians(yaw_deg))
    corners = [(-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2)]
    return [(cx + x * c - y * s, cy + x * s + y * c) for x, y in corners]


def route_map(scene: dict[str, Any], out: Path, size: int = 1600, samples_per_s: int = 8) -> Path:
    """Top-down map evaluated from the same kinematics the renderer uses (lines never enter the video)."""
    from ..blender import kinematics as K

    duration = float(scene["duration_s"])
    time_map = K.TimeMap.from_spec(scene.get("time_map"), duration)
    n = max(2, int(duration * samples_per_s))
    times = [duration * i / (n - 1) for i in range(n)]
    actor_tracks = {a["id"]: K.Track(a["path"]["keys"], a["path"].get("interpolation", "cubic"))
                    for a in scene.get("actors", [])}
    actor_lines = {aid: [track.at(time_map.source(t)) for t in times] for aid, track in actor_tracks.items()}
    cam_lines: list[tuple[str, list[tuple[float, float, float]]]] = []
    for shot in scene.get("shots", []):
        track = K.Track(shot["camera"]["keys"], shot["camera"].get("interpolation", "cubic"))
        st, et = float(shot["start_s"]), float(shot["end_s"])
        pts = [track.at(st + (et - st) * i / 15) for i in range(16)]
        cam_lines.append((shot["id"], pts))

    blocks = [b for b in scene.get("blocks", []) if b.get("role") != "ground"]
    xs, ys = [], []
    for b in blocks:
        for x, y in _rotated_rect(b["center"][0], b["center"][1], b["size"][0], b["size"][1],
                                  (b.get("rotation_deg") or [0, 0, 0])[2]):
            xs.append(x)
            ys.append(y)
    for pts in list(actor_lines.values()) + [p for _, p in cam_lines]:
        xs += [p[0] for p in pts]
        ys += [p[1] for p in pts]
    if not xs:
        xs, ys = [-10, 10], [-10, 10]
    margin = 0.08
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0) * (1 + 2 * margin)
    cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
    scale = size / span

    def px(x: float, y: float) -> tuple[float, float]:
        return ((x - cx) * scale + size / 2, size / 2 - (y - cy) * scale)

    img = Image.new("RGB", (size, size + 60), "#f4f5f7")
    draw = ImageDraw.Draw(img)
    for b in blocks:
        poly = [px(x, y) for x, y in _rotated_rect(b["center"][0], b["center"][1], b["size"][0], b["size"][1],
                                                   (b.get("rotation_deg") or [0, 0, 0])[2])]
        fill = "#dfe3e8" if b.get("role") in ("floor", "zone") else "#b9bec6"
        draw.polygon(poly, fill=fill, outline="#8a9099")
    palette = ["#d9363e", "#2f6fdb", "#e0a800", "#2e9e5b", "#8e44ad", "#555555"]
    for i, (aid, pts) in enumerate(actor_lines.items()):
        actor = next(a for a in scene["actors"] if a["id"] == aid)
        color = actor.get("color") or palette[i % len(palette)]
        draw.line([px(p[0], p[1]) for p in pts], fill=color, width=5)
        sx, sy = px(pts[0][0], pts[0][1])
        draw.ellipse([sx - 9, sy - 9, sx + 9, sy + 9], outline=color, width=4)
        draw.text((sx + 12, sy - 10), aid, fill=color, font=font(20))
    label_font = font(18)
    for shot_id, pts in cam_lines:
        line = [px(p[0], p[1]) for p in pts]
        for j in range(0, len(line) - 1, 2):
            draw.line([line[j], line[j + 1]], fill="#f08a00", width=4)
        draw.text((line[0][0] + 6, line[0][1] + 4), shot_id, fill="#b25f00", font=label_font)
    bar_m = 10 ** math.floor(math.log10(span / 5)) if span > 5 else 1
    draw.line([(30, size + 30), (30 + bar_m * scale, size + 30)], fill="#222222", width=4)
    draw.text((30, size + 36), f"{bar_m:g} m", fill="#222222", font=label_font)
    draw.text((260, size + 20), "实线：人物路线（源秒）   虚线：各镜头相机路径（成片秒）", fill="#333333", font=label_font)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
