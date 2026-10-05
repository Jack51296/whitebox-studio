"""Render settings and frame output (runs inside Blender)."""

from __future__ import annotations

import math
from pathlib import Path

import bpy


def eevee_engine_id() -> str:
    items = {e.identifier for e in bpy.types.RenderSettings.bl_rna.properties["engine"].enum_items}
    return "BLENDER_EEVEE_NEXT" if "BLENDER_EEVEE_NEXT" in items else "BLENDER_EEVEE"


def configure(engine: str = "workbench", background: tuple = (0.82, 0.82, 0.82)) -> dict:
    scene = bpy.context.scene
    world = scene.world or bpy.data.worlds.new("WBS_World")
    scene.world = world
    world.color = background
    scene.view_settings.view_transform = "Standard"
    scene.view_settings.look = "None"
    scene.render.film_transparent = False
    scene.render.use_motion_blur = False
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGB"
    scene.render.image_settings.color_depth = "8"
    scene.render.use_overwrite = True
    scene.render.use_placeholder = False
    scene.render.use_file_extension = True

    if engine == "workbench":
        scene.render.engine = "BLENDER_WORKBENCH"
        shading = scene.display.shading
        shading.light = "STUDIO"
        shading.color_type = "MATERIAL"
        shading.show_shadows = True
        shading.shadow_intensity = 0.35
        shading.show_cavity = True
        shading.cavity_type = "WORLD"
        shading.show_specular_highlight = False
        scene.display.light_direction = (0.45, -0.35, 0.82)
        scene.display.render_aa = "8"
        return {"engine": "BLENDER_WORKBENCH", "lighting": "studio + shadow + cavity"}

    scene.render.engine = eevee_engine_id()
    if world.node_tree is None:
        try:
            world.use_nodes = True
        except (AttributeError, TypeError):
            pass
    if world.node_tree is not None:
        bg = world.node_tree.nodes.get("Background")
        if bg is not None:
            bg.inputs["Color"].default_value = (*background, 1.0)
            bg.inputs["Strength"].default_value = 0.9
    for name, energy, rot in (("WBS_Key", 3.0, (50, 0, 35)), ("WBS_Fill", 1.0, (60, 0, -145))):
        light = bpy.data.lights.new(name, "SUN")
        light.energy = energy
        light.angle = math.radians(12)
        obj = bpy.data.objects.new(name, light)
        obj.rotation_euler = tuple(math.radians(v) for v in rot)
        scene.collection.objects.link(obj)
    if hasattr(scene, "eevee"):
        scene.eevee.taa_render_samples = 16
    return {"engine": scene.render.engine, "lighting": "two_soft_plus_ambient"}


def render_frames(out_dir: Path, frames: list[int] | None = None) -> int:
    """Render all frames (animation) or an explicit list to ``out_dir/f####.png``."""
    scene = bpy.context.scene
    out_dir.mkdir(parents=True, exist_ok=True)
    if frames is None:
        scene.render.filepath = str(out_dir / "f")
        bpy.ops.render.render(animation=True)
        return scene.frame_end - scene.frame_start + 1
    for f in frames:
        scene.frame_set(f)
        scene.render.filepath = str(out_dir / f"f{f:04d}.png")
        bpy.ops.render.render(write_still=True)
    return len(frames)
