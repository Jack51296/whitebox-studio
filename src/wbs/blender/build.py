"""Build a white-model scene in Blender from scene.json (runs inside Blender; imports bpy).

Low precision only ([D6]): every subject is a rigid proxy made of primitives that translates and
turns about Z; birds may flap two wing plates. Every actor and camera transform is keyed on every
frame from ``kinematics.SceneEvaluator`` so Blender and the Python QC evaluate identical motion.
Blender 5 stores keys in layered actions; this module never touches ``action.fcurves``.
"""

from __future__ import annotations

import math
from pathlib import Path

import bmesh
import bpy
import kinematics as K
from mathutils import Euler, Matrix, Quaternion, Vector

PALETTES = {
    "white": {"ground": 0.75, "block": 0.88, "floor": 0.80, "actor": 0.96},
    "grey": {"ground": 0.50, "block": 0.66, "floor": 0.58, "actor": 0.86},
    "identity": {"ground": 0.55, "block": 0.72, "floor": 0.62, "actor": 0.85},
}
MARKER_RGBA = (0.06, 0.06, 0.06, 1.0)


def srgb_to_linear(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def hex_rgba(value: str) -> tuple[float, float, float, float]:
    value = value.lstrip("#")
    r, g, b = (int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return (srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), 1.0)


def grey(v: float) -> tuple[float, float, float, float]:
    return (v, v, v, 1.0)


class Materials:
    def __init__(self) -> None:
        self.cache: dict[str, bpy.types.Material] = {}

    def get(self, key: str, rgba: tuple[float, float, float, float]) -> bpy.types.Material:
        if key in self.cache:
            return self.cache[key]
        mat = bpy.data.materials.new(f"WBS_{key}")
        mat.diffuse_color = rgba
        mat.roughness = 0.85
        mat.metallic = 0.0
        try:
            mat.use_nodes = True
        except (AttributeError, TypeError):
            pass
        tree = getattr(mat, "node_tree", None)
        if tree is not None:
            bsdf = tree.nodes.get("Principled BSDF")
            if bsdf is not None:
                bsdf.inputs["Base Color"].default_value = rgba
                bsdf.inputs["Roughness"].default_value = 0.85
        self.cache[key] = mat
        return mat


# --------------------------------------------------------------------------- mesh primitives
def _m(offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), rot_z_deg: float = 0.0, rot=None) -> Matrix:
    r = rot if rot is not None else Matrix.Rotation(math.radians(rot_z_deg), 4, "Z")
    return Matrix.Translation(Vector(offset)) @ r @ Matrix.Diagonal((*scale, 1.0))


def bm_box(bm, size, offset=(0.0, 0.0, 0.0), rot_z_deg: float = 0.0) -> None:
    bmesh.ops.create_cube(bm, size=1.0, matrix=_m(offset, size, rot_z_deg))


def bm_cyl(bm, r1, r2, depth, offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), segments=24, rot=None,
           rot_z_deg: float = 0.0) -> None:
    bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=segments, radius1=r1, radius2=r2,
                          depth=depth, matrix=_m(offset, scale, rot_z_deg, rot))


def bm_sphere(bm, r, offset=(0.0, 0.0, 0.0), scale=(1.0, 1.0, 1.0), segments=(24, 12)) -> None:
    res = bmesh.ops.create_uvsphere(bm, u_segments=segments[0], v_segments=segments[1], radius=r,
                                    matrix=_m(offset, scale))
    for face in {f for v in res["verts"] for f in v.link_faces}:
        face.smooth = True


def bm_octahedron(bm, r, offset=(0.0, 0.0, 0.0)) -> None:
    ox, oy, oz = offset
    bm_cyl(bm, r, 0.0, r, offset=(ox, oy, oz + r / 2), segments=4)
    bm_cyl(bm, r, 0.0, r, offset=(ox, oy, oz - r / 2), segments=4, rot=Matrix.Rotation(math.pi, 4, "X"))


def bm_ramp(bm, size) -> None:
    x, y, z = (s / 2 for s in size)
    v = [bm.verts.new(p) for p in ((-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z), (-x, y, z), (x, y, z))]
    for face in ((0, 3, 2, 1), (2, 3, 4, 5), (0, 1, 5, 4), (0, 4, 3), (1, 2, 5)):
        bm.faces.new([v[i] for i in face])
    bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))


def bm_capsule(bm, r, height, offset=(0.0, 0.0, 0.0), scale_xy=(1.0, 1.0)) -> None:
    ox, oy, oz = offset
    if height <= 2 * r:
        bm_sphere(bm, 1.0, offset=(ox, oy, oz + height / 2), scale=(r * scale_xy[0], r * scale_xy[1], height / 2))
        return
    sx, sy = scale_xy
    bm_cyl(bm, r, r, height - 2 * r, offset=(ox, oy, oz + height / 2), scale=(sx, sy, 1.0))
    bm_sphere(bm, r, offset=(ox, oy, oz + r), scale=(sx, sy, 1.0))
    bm_sphere(bm, r, offset=(ox, oy, oz + height - r), scale=(sx, sy, 1.0))


def new_object(name: str, bm, mat, collection, parent=None, location=(0.0, 0.0, 0.0), rotation=(0.0, 0.0, 0.0)):
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    mesh.materials.append(mat)
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    if parent is not None:
        obj.parent = parent
    obj.location = location
    obj.rotation_euler = rotation
    return obj


def collection(name: str):
    coll = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    if coll.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(coll)
    return coll


# --------------------------------------------------------------------------- blocks
def build_block(block: dict, mats: Materials, coll, palette: str):
    bm = bmesh.new()
    sx, sy, sz = block["size"]
    shape = block.get("shape", "box")
    if shape in ("box", "plane"):
        bm_box(bm, (sx, sy, sz))
    elif shape == "cylinder":
        bm_cyl(bm, 0.5, 0.5, 1.0, scale=(sx, sy, sz), segments=32)
    elif shape == "cone":
        bm_cyl(bm, 0.5, 0.0, 1.0, scale=(sx, sy, sz), segments=32)
    elif shape == "sphere":
        bm_sphere(bm, 0.5, scale=(sx, sy, sz))
    elif shape == "capsule":
        r = min(sx, sy) / 2
        bm_capsule(bm, r, sz, offset=(0.0, 0.0, -sz / 2), scale_xy=(sx / (2 * r), sy / (2 * r)))
    elif shape == "ramp":
        bm_ramp(bm, (sx, sy, sz))
    else:
        raise ValueError(f"unknown block shape {shape}")
    role = block.get("role", "structure")
    tones = PALETTES[palette]
    if palette == "identity" and block.get("color"):
        key, rgba = f"block_{block['color']}", hex_rgba(block["color"])
    elif role == "ground":
        key, rgba = "ground", grey(tones["ground"])
    elif role == "floor":
        key, rgba = "floor", grey(tones["floor"])
    else:
        key, rgba = "block", grey(tones["block"])
    rot = tuple(math.radians(v) for v in (block.get("rotation_deg") or (0.0, 0.0, 0.0)))
    return new_object(f"BLOCK_{block['id']}", bm, mats.get(key, rgba), coll, location=tuple(block["center"]),
                      rotation=rot)


# --------------------------------------------------------------------------- actors
def _pawn(bm_body, bm_head, bm_mark, height: float, radius: float, head: str, body: str) -> None:
    body_h, rb, rh = 0.74 * height, radius * 0.8, 0.1 * height
    head_z = body_h + 0.02 * height + rh
    if body == "capsule":
        bm_capsule(bm_body, rb, body_h)
    elif body == "box":
        bm_box(bm_body, (2 * rb, 1.4 * rb, body_h), offset=(0.0, 0.0, body_h / 2))
    elif body == "cylinder":
        bm_cyl(bm_body, rb, rb, body_h, offset=(0.0, 0.0, body_h / 2))
    elif body == "cone":
        bm_cyl(bm_body, rb, rb * 0.35, body_h, offset=(0.0, 0.0, body_h / 2))
    elif body == "taper":
        bm_cyl(bm_body, rb * 1.2, rb * 0.7, body_h, offset=(0.0, 0.0, body_h / 2), segments=4, rot_z_deg=45)
    elif body == "ellipsoid":
        bm_sphere(bm_body, 1.0, offset=(0.0, 0.0, body_h / 2), scale=(rb, rb * 0.75, body_h / 2))
    elif body == "bipyramid":
        bm_cyl(bm_body, rb * 0.25, rb, body_h / 2, offset=(0.0, 0.0, body_h / 4), segments=6)
        bm_cyl(bm_body, rb, rb * 0.3, body_h / 2, offset=(0.0, 0.0, body_h * 0.75), segments=6)
    else:
        raise ValueError(f"unknown body {body}")
    if head == "cube":
        bm_box(bm_head, (1.7 * rh, 1.7 * rh, 1.7 * rh), offset=(0.0, 0.0, head_z))
    elif head == "octahedron":
        bm_octahedron(bm_head, rh * 1.2, offset=(0.0, 0.0, head_z))
    elif head == "capsule":
        bm_sphere(bm_head, rh, offset=(0.0, 0.0, head_z), scale=(1.0, 1.0, 1.3))
    else:
        bm_sphere(bm_head, rh, offset=(0.0, 0.0, head_z))
    bm_box(bm_mark, (0.5 * rh, 0.6 * rh, 0.35 * rh), offset=(0.0, rh * 0.95, head_z))


def build_actor(actor: dict, mats: Materials, coll, palette: str, fbx_library: str | None = None) -> dict:
    aid, kind = actor["id"], actor["kind"]
    height, radius = float(actor.get("height_m", 1.7)), float(actor.get("radius_m", 0.35))
    root = bpy.data.objects.new(f"ACTOR_{aid}", None)
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = max(0.3, radius)
    coll.objects.link(root)
    color = hex_rgba(actor["color"]) if palette == "identity" and actor.get("color") else grey(PALETTES[palette]["actor"])
    mat, mark = mats.get(f"actor_{aid}", color), mats.get("marker", MARKER_RGBA)
    parts, wings = [], []

    def part(name: str, fill, material=mat, location=(0.0, 0.0, 0.0)):
        bm = bmesh.new()
        fill(bm)
        obj = new_object(f"ACTOR_{aid}_{name}", bm, material, coll, parent=root, location=location)
        parts.append(obj)
        return obj

    if kind == "pawn":
        body, head, marker = bmesh.new(), bmesh.new(), bmesh.new()
        _pawn(body, head, marker, height, radius, actor.get("head", "sphere"), actor.get("body", "capsule"))
        for name, bm, material in (("body", body, mat), ("head", head, mat), ("facing", marker, mark)):
            parts.append(new_object(f"ACTOR_{aid}_{name}", bm, material, coll, parent=root))
    elif kind == "robot":
        body_h = 0.62 * height
        part("body", lambda bm: bm_box(bm, (1.8 * radius, radius, body_h), offset=(0.0, 0.0, body_h / 2)))
        hs = 0.2 * height
        part("head", lambda bm: bm_box(bm, (hs, hs, hs), offset=(0.0, 0.0, body_h + 0.03 * height + hs / 2)))
        part("facing", lambda bm: bm_box(bm, (hs * 0.8, hs * 0.2, hs * 0.25),
                                         offset=(0.0, hs / 2, body_h + 0.03 * height + hs * 0.6)), mark)
    elif kind == "block_animal":
        length, torso_h = 1.5 * radius, 0.4 * height
        leg_h = 0.5 * height
        part("torso", lambda bm: bm_box(bm, (0.45 * radius, length, torso_h), offset=(0.0, 0.0, leg_h + torso_h / 2)))
        hs = 0.3 * height
        part("head", lambda bm: bm_box(bm, (hs, hs, hs), offset=(0.0, length / 2 + hs / 2, leg_h + torso_h)))
        part("facing", lambda bm: bm_box(bm, (hs * 0.4, hs * 0.3, hs * 0.3),
                                         offset=(0.0, length / 2 + hs, leg_h + torso_h)), mark)

        def legs(bm):
            for sx in (-1, 1):
                for sy in (-1, 1):
                    bm_box(bm, (0.12, 0.12, leg_h), offset=(sx * 0.15 * radius, sy * 0.38 * length, leg_h / 2))
        part("legs", legs)
    elif kind == "block_bird":
        part("body", lambda bm: bm_sphere(bm, 1.0, scale=(0.1 * radius, 0.42 * radius, 0.09 * radius)))
        part("head", lambda bm: bm_sphere(bm, 0.08 * radius, offset=(0.0, 0.42 * radius, 0.04 * radius)))
        part("facing", lambda bm: bm_box(bm, (0.04 * radius, 0.12 * radius, 0.03 * radius),
                                         offset=(0.0, 0.54 * radius, 0.04 * radius)), mark)
        part("tail", lambda bm: bm_box(bm, (0.2 * radius, 0.18 * radius, 0.015), offset=(0.0, -0.48 * radius, 0.0)))
        for side in (-1, 1):
            wing = part(f"wing_{'L' if side < 0 else 'R'}",
                        lambda bm, s=side: bm_box(bm, (0.9 * radius, 0.3 * radius, 0.02), offset=(s * 0.45 * radius, 0.0, 0.0)),
                        location=(side * 0.08 * radius, 0.0, 0.02 * radius))
            wing.rotation_mode = "XYZ"
            wings.append((wing, side))
    elif kind == "block_fish":
        length = 1.6 * radius
        part("body", lambda bm: bm_sphere(bm, 1.0, scale=(0.12 * length, 0.5 * length, 0.18 * length)))
        part("tail", lambda bm: bm_box(bm, (0.02, 0.2 * length, 0.28 * length), offset=(0.0, -0.58 * length, 0.0)))
        part("facing", lambda bm: bm_box(bm, (0.06 * length, 0.05 * length, 0.05 * length),
                                         offset=(0.0, 0.5 * length, 0.02 * length)), mark)
    elif kind == "vehicle":
        length, width, wheel = 2 * radius, 1.9, 0.35
        part("body", lambda bm: bm_box(bm, (width, length, 0.65), offset=(0.0, 0.0, 0.3 + 0.325)))
        part("cabin", lambda bm: bm_box(bm, (width * 0.85, length * 0.45, 0.55), offset=(0.0, -0.1 * length, 0.95 + 0.275)))
        part("facing", lambda bm: bm_box(bm, (width * 0.6, 0.05, 0.14), offset=(0.0, length / 2 + 0.02, 0.75)), mark)

        def wheels(bm):
            for sx in (-1, 1):
                for sy in (-1, 1):
                    bm_cyl(bm, wheel, wheel, 0.28, offset=(sx * (width / 2 - 0.1), sy * (length / 2 - 0.8), wheel),
                           rot=Matrix.Rotation(math.pi / 2, 4, "Y"))
        part("wheels", wheels, mark)
    elif kind == "prop":
        s = height / 0.6
        part("body", lambda bm: bm_box(bm, (0.35 * s, 0.25 * s, 0.55 * s), offset=(0.0, 0.0, 0.275 * s)))
        part("facing", lambda bm: bm_box(bm, (0.2 * s, 0.02, 0.06 * s), offset=(0.0, 0.125 * s + 0.01, 0.4 * s)), mark)
    elif kind == "fbx":
        parts.extend(_import_fbx(actor, root, coll, fbx_library, height, mat))
    else:
        raise ValueError(f"unknown actor kind {kind}")
    return {"root": root, "parts": parts, "wings": wings, "flap_hz": float(actor.get("flap_hz") or 0.0)}


def _import_fbx(actor: dict, root, coll, library: str | None, height: float, mat) -> list:
    path = Path(actor["fbx"])
    if not path.is_absolute() and library:
        path = Path(library) / path
    if not path.exists():
        raise FileNotFoundError(f"FBX not found: {path} (configure fbx_library)")
    before = set(bpy.data.objects)
    if hasattr(bpy.ops.wm, "fbx_import"):
        bpy.ops.wm.fbx_import(filepath=str(path))
    else:
        bpy.ops.import_scene.fbx(filepath=str(path))
    new = [o for o in bpy.data.objects if o not in before]
    meshes = [o for o in new if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"{path}: no mesh imported")
    zs = [(o.matrix_world @ Vector(c)).z for o in meshes for c in o.bound_box]
    scale = height / max(max(zs) - min(zs), 1e-6)
    for obj in new:
        for c in obj.users_collection:
            c.objects.unlink(obj)
        coll.objects.link(obj)
        if obj.parent is None:
            obj.parent = root
            obj.scale = tuple(v * scale for v in obj.scale)
        if obj.type == "MESH":
            obj.data.materials.clear()
            obj.data.materials.append(mat)
    return meshes


# --------------------------------------------------------------------------- cameras
def camera_matrix(state: dict, prev_q: Quaternion | None) -> tuple[Vector, Quaternion]:
    pos, aim = Vector(state["pos"]), Vector(state["aim"])
    direction = aim - pos
    if direction.length < 1e-4:
        q = prev_q.copy() if prev_q is not None else Quaternion()
    else:
        q = direction.to_track_quat("-Z", "Y")
    if state.get("roll_deg"):
        q = q @ Quaternion((0.0, 0.0, 1.0), math.radians(state["roll_deg"]))
    m = Matrix.Translation(pos) @ q.to_matrix().to_4x4()
    tr, rot = state.get("shake_translation") or (0, 0, 0), state.get("shake_rotation_deg") or (0, 0, 0)
    if any(tr) or any(rot):
        m = m @ (Matrix.Translation(Vector(tr)) @ Euler(tuple(math.radians(v) for v in rot), "XYZ").to_matrix().to_4x4())
    loc, quat, _ = m.decompose()
    if prev_q is not None and prev_q.dot(quat) < 0:
        quat.negate()
    return loc, quat


def build_scene(scene: dict, fbx_library: str | None = None) -> dict:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bl_scene = bpy.context.scene
    fps, frames = int(scene["fps"]), int(round(scene["duration_s"] * scene["fps"]))
    bl_scene.render.fps, bl_scene.render.fps_base = fps, 1.0
    bl_scene.frame_start, bl_scene.frame_end = 1, frames
    bl_scene.render.resolution_x, bl_scene.render.resolution_y = scene["resolution"]
    bl_scene.render.resolution_percentage = 100
    bl_scene.unit_settings.system = "METRIC"

    mats = Materials()
    palette = scene.get("palette", "grey")
    blocks_coll, actors_coll, cams_coll = collection("WBS_Blocks"), collection("WBS_Actors"), collection("WBS_Cameras")
    blocks = {b["id"]: build_block(b, mats, blocks_coll, palette) for b in scene.get("blocks", [])}
    actors = {a["id"]: build_actor(a, mats, actors_coll, palette, fbx_library) for a in scene.get("actors", [])}

    ev = K.SceneEvaluator(scene)
    for f in range(1, frames + 1):
        t = ev.frame_time(f)
        for aid, handle in actors.items():
            pos, yaw = ev.actor_state(aid, t)
            root = handle["root"]
            root.location = pos
            root.rotation_euler = (0.0, 0.0, math.radians(yaw))
            root.keyframe_insert(data_path="location", frame=f)
            root.keyframe_insert(data_path="rotation_euler", frame=f)
            if handle["wings"] and handle["flap_hz"] > 0:
                angle = math.radians(25.0) * math.sin(2 * math.pi * handle["flap_hz"] * ev.source_time(t))
                for wing, side in handle["wings"]:
                    wing.rotation_euler = (0.0, -side * angle, 0.0)
                    wing.keyframe_insert(data_path="rotation_euler", frame=f)

    cameras = []
    for i, shot in enumerate(scene["shots"]):
        data = bpy.data.cameras.new(f"CAMERA_{shot['id']}")
        data.lens = float(shot.get("lens_mm", 32.0))
        data.sensor_width = 36.0
        data.sensor_fit = "HORIZONTAL"
        data.clip_start, data.clip_end = 0.05, 2000.0
        data.dof.use_dof = False
        cam = bpy.data.objects.new(f"CAMERA_{shot['id']}", data)
        cams_coll.objects.link(cam)
        cam.rotation_mode = "QUATERNION"
        first, last = ev.shot_frames(i)
        prev = None
        for f in range(first, last + 1):
            loc, quat = camera_matrix(ev.camera_state(ev.frame_time(f), index=i), prev)
            prev = quat
            cam.location, cam.rotation_quaternion = loc, quat
            cam.keyframe_insert(data_path="location", frame=f)
            cam.keyframe_insert(data_path="rotation_quaternion", frame=f)
        marker = bl_scene.timeline_markers.new(shot["id"], frame=first)
        marker.camera = cam
        cameras.append(cam)
    bl_scene.camera = cameras[0]
    return {"blocks": blocks, "actors": actors, "cameras": cameras, "evaluator": ev}
