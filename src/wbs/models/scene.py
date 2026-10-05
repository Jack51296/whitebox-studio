"""scene.json v1: everything the Blender builder needs; builders read JSON only, nothing is hard-coded.

Merges the reverse pipeline's ``camera_solve`` / ``scene_blocks`` ([D2]) with the story packages'
project-1.0 production spec (whitebox-world-studio): metric world, Z up, +Y north; actor paths on the
source clock, cameras on the edit clock, optional monotone time map between them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vec3 = tuple[float, float, float]
Key4 = tuple[float, float, float, float]
Key2 = tuple[float, float]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class TimeMapSpec(_Strict):
    interpolation: Literal["linear", "monotone_cubic"] = "linear"
    knots: list[Key2] = Field(min_length=2)


class Block(_Strict):
    id: str
    shape: Literal["box", "cylinder", "sphere", "cone", "capsule", "ramp", "plane"] = "box"
    center: Vec3
    size: Vec3
    rotation_deg: Vec3 = (0.0, 0.0, 0.0)
    role: str = "structure"
    color: str | None = None
    collision: bool = True
    label: str = ""
    group: str | None = None


class PathSpec(_Strict):
    keys: list[Key4] = Field(min_length=1)
    interpolation: Literal["cubic", "smooth", "linear"] = "cubic"


class Actor(_Strict):
    id: str
    kind: Literal["pawn", "block_animal", "block_bird", "block_fish", "vehicle", "robot", "prop", "fbx"] = "pawn"
    label: str = ""
    role: str = ""
    height_m: float = 1.7
    radius_m: float = 0.35
    head: Literal["sphere", "cube", "octahedron", "capsule"] = "sphere"
    body: Literal["capsule", "box", "cylinder", "cone", "taper", "ellipsoid", "bipyramid"] = "capsule"
    color: str | None = None
    facing_marker: bool = True
    fbx: str | None = None
    path: PathSpec
    yaw_keys: list[Key2] = Field(default_factory=list)
    max_yaw_rate_dps: float | None = Field(default=None, gt=0)
    flap_hz: float = 0.0


class CameraTrack(_Strict):
    keys: list[Key4] = Field(min_length=1)
    interpolation: Literal["cubic", "smooth", "linear"] = "cubic"
    aim_keys: list[Key4] = Field(default_factory=list)
    aim_actor: str | None = None
    aim_offset: Vec3 = (0.0, 0.0, 1.2)
    roll_keys: list[Key2] = Field(default_factory=list)
    responses: list[dict[str, Any]] = Field(default_factory=list)


class Shot(_Strict):
    id: str
    start_s: float
    end_s: float
    lens_mm: float = 32.0
    camera: CameraTrack
    title: str = ""
    action: str = ""
    framing: str = ""
    angle: str = ""
    move: str = ""


class Event(_Strict):
    id: str
    at_s: float
    mechanism: str = ""
    choice: str = ""
    consequence: str = ""
    role: Literal["beat", "setup", "twist"] = "beat"
    targets: list[str] = Field(default_factory=list)
    focus_region: tuple[float, float, float, float, float, float] | None = None


class RouteBeat(_Strict):
    """One 路线与空间交接 line of the director card: condition → action → visible change."""

    start_s: float
    end_s: float
    condition: str
    action: str
    change: str


class RenderSpec(_Strict):
    engine: Literal["workbench", "eevee"] = "workbench"
    background: Vec3 = (0.82, 0.82, 0.82)
    lighting: Literal["two_soft_plus_ambient"] = "two_soft_plus_ambient"


class SceneSpec(_Strict):
    schema_version: str = Field("wbs.scene/1.0", alias="schema")
    id: str
    title: str
    fps: int = 24
    duration_s: float = Field(gt=0)
    resolution: tuple[int, int]
    precision: Literal["low", "medium"] = "low"
    palette: Literal["white", "grey", "identity"] = "grey"
    time_map: TimeMapSpec | None = None
    blocks: list[Block] = Field(default_factory=list)
    actors: list[Actor] = Field(default_factory=list)
    shots: list[Shot] = Field(min_length=1)
    events: list[Event] = Field(default_factory=list)
    beats: list[RouteBeat] = Field(default_factory=list)
    render: RenderSpec = Field(default_factory=RenderSpec)
    source: dict[str, Any] = Field(default_factory=dict)
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def frame_count(self) -> int:
        return int(round(self.duration_s * self.fps))

    def to_json_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json", by_alias=True)

    @model_validator(mode="after")
    def _check(self) -> SceneSpec:
        for name, items in (("block", self.blocks), ("actor", self.actors), ("shot", self.shots),
                            ("event", self.events)):
            ids = [i.id for i in items]
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            if dupes:
                raise ValueError(f"duplicate {name} ids: {dupes}")
        eps, fps = 1e-6, self.fps
        if abs(self.shots[0].start_s) > eps:
            raise ValueError("first shot must start at 0")
        for a, b in zip(self.shots, self.shots[1:]):
            if abs(a.end_s - b.start_s) > eps:
                raise ValueError(f"shots must be contiguous: {a.id} ends {a.end_s}, {b.id} starts {b.start_s}")
        if abs(self.shots[-1].end_s - self.duration_s) > eps:
            raise ValueError("last shot must end at duration_s")
        actor_ids = {a.id for a in self.actors}
        for shot in self.shots:
            if shot.end_s - shot.start_s < 1.0 / fps - eps:
                raise ValueError(f"{shot.id} shorter than one frame")
            if abs(shot.start_s * fps - round(shot.start_s * fps)) > 1e-4:
                raise ValueError(f"{shot.id} start {shot.start_s}s is not on a frame boundary at {fps}fps")
            if not shot.camera.aim_keys and shot.camera.aim_actor not in actor_ids:
                raise ValueError(f"{shot.id}: camera needs aim_keys or a valid aim_actor")
            if not 5.0 <= shot.lens_mm <= 300.0:
                raise ValueError(f"{shot.id}: lens_mm {shot.lens_mm} out of range")
        if self.time_map is not None:
            first, last = self.time_map.knots[0], self.time_map.knots[-1]
            if abs(first[0]) > eps or abs(last[0] - self.duration_s) > eps:
                raise ValueError("time_map knots must span edit time 0..duration_s")
        for actor in self.actors:
            if actor.kind == "fbx" and not actor.fbx:
                raise ValueError(f"actor {actor.id}: kind fbx needs an fbx path")
        for beat in self.beats:
            if not 0 <= beat.start_s < beat.end_s <= self.duration_s + eps:
                raise ValueError(f"route beat {beat.start_s}–{beat.end_s}s outside 0..duration_s")
        known = actor_ids | {b.id for b in self.blocks} | {b.group for b in self.blocks if b.group}
        for event in self.events:
            unknown = [t for t in event.targets if t not in known]
            if unknown:
                raise ValueError(f"event {event.id}: targets {unknown} are not block, block group or actor ids")
        return self
