"""Control passes for video-to-video models (runs inside Blender): segmentation and depth.

White-box scenes know every object exactly, so these are clean control signals (Cosmos-Transfer / Wan VACE
use depth, segmentation and edges). Edges are computed from the normal render outside Blender.
"""

from __future__ import annotations

import colorsys
from pathlib import Path

import bpy
from render_ops import eevee_engine_id, render_frames

ROLE_COLORS = {"ground": (0.25, 0.25, 0.25), "structure_line": (0.55, 0.35, 0.15), "depth_occupancy": (0.45, 0.45, 0.60),
               "wall": (0.40, 0.55, 0.40), "prop": (0.70, 0.60, 0.20)}


def _raw_view(scene) -> None:
    items = {e.identifier for e in type(scene.view_settings).bl_rna.properties["view_transform"].enum_items}
    scene.view_settings.view_transform = "Raw" if "Raw" in items else "Standard"


def _instance_color(i: int) -> tuple[float, float, float]:
    return colorsys.hsv_to_rgb((0.07 + i * 0.618034) % 1.0, 0.85, 1.0)


def _block_color(role: str, i: int) -> tuple[float, float, float]:
    if role in ROLE_COLORS:
        return ROLE_COLORS[role]
    h = (0.55 + i * 0.137) % 1.0
    return colorsys.hsv_to_rgb(h, 0.25, 0.55)


def segmentation(scene_json: dict, out_dir: Path, frames: list[int] | None) -> dict:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light = "FLAT"
    shading.color_type = "OBJECT"
    for attr in ("show_shadows", "show_cavity", "show_object_outline", "show_specular_highlight", "show_xray"):
        if hasattr(shading, attr):
            setattr(shading, attr, False)
    scene.display.render_aa = "OFF"
    _raw_view(scene)
    scene.world.color = (0.0, 0.0, 0.0)
    roles = {b["id"]: b.get("role") or "block" for b in scene_json.get("blocks", [])}
    actors = [a["id"] for a in scene_json.get("actors", [])]
    legend = {}
    for i, obj in enumerate(sorted(scene.objects, key=lambda o: o.name)):
        if obj.type != "MESH":
            continue
        if obj.name.startswith("ACTOR_"):
            aid = obj.name.split("_")[1]
            color, cls = _instance_color(actors.index(aid) if aid in actors else i), f"actor:{aid}"
        elif obj.name.startswith("BLOCK_"):
            role = roles.get(obj.name[len("BLOCK_"):], "block")
            color, cls = _block_color(role, i), f"block:{role}"
        else:
            color, cls = (0.5, 0.5, 0.5), "other"
        obj.color = (*color, 1.0)
        legend[obj.name] = {"class": cls, "rgb": [round(c * 255) for c in color]}
    n = render_frames(out_dir, frames)
    return {"frames": n, "legend": legend, "encoding": "Workbench FLAT + per-object colour, Raw view transform, no AA"}


def depth(out_dir: Path, frames: list[int] | None, near: float = 0.1, far: float = 60.0) -> dict:
    scene = bpy.context.scene
    scene.render.engine = eevee_engine_id()
    mat = bpy.data.materials.new("WBS_DEPTH")
    try:
        mat.use_nodes = True
    except (AttributeError, TypeError):
        pass
    nodes, links = mat.node_tree.nodes, mat.node_tree.links
    nodes.clear()
    cam = nodes.new("ShaderNodeCameraData")
    remap = nodes.new("ShaderNodeMapRange")
    emit = nodes.new("ShaderNodeEmission")
    out = nodes.new("ShaderNodeOutputMaterial")
    remap.clamp = True
    for key, value in (("From Min", near), ("From Max", far), ("To Min", 1.0), ("To Max", 0.0)):
        remap.inputs[key].default_value = value
    links.new(cam.outputs["View Z Depth"], remap.inputs["Value"])
    links.new(remap.outputs["Result"], emit.inputs["Color"])
    links.new(emit.outputs["Emission"], out.inputs["Surface"])
    bpy.context.view_layer.material_override = mat
    scene.world.color = (0.0, 0.0, 0.0)
    if scene.world.node_tree is not None:
        bg = scene.world.node_tree.nodes.get("Background")
        if bg is not None:
            bg.inputs["Strength"].default_value = 0.0
    _raw_view(scene)
    n = render_frames(out_dir, frames)
    bpy.context.view_layer.material_override = None
    return {"frames": n, "near_m": near, "far_m": far,
            "encoding": "value = 1 − (view_z − near)/(far − near), clamped; near = white, sky = black; Raw view transform"}


def render_passes(scene_json: dict, out_dir: Path, names: list[str], frames: list[int] | None) -> dict:
    result = {}
    for name in names:
        if name == "seg":
            result["seg"] = segmentation(scene_json, out_dir / "seg", frames)
        elif name == "depth":
            result["depth"] = depth(out_dir / "depth", frames, far=float(scene_json.get("render", {}).get("depth_far_m", 60.0)))
    return result
