"""StoryPlan: what a story-driven planning model returns ([D5] flow; fields from the team's director cards).

The event chain mirrors the team's authored story data (whitebox-world-studio ``制作脚本/stories.py``):
events carry an action (``choice``) and a visible consequence, ``twist`` indexes the turning event and
``setup`` the earlier events that make the turn credible ("转折依据必须在前面交代"), and ``beats`` are the
route/space hand-overs printed as 路线与空间交接 in the director card.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..camera_language import ANGLES, FRAMINGS, MOVES
from .scene import Block, Event, Key4, RouteBeat, TimeMapSpec, Vec3

FramingName = Literal[("",) + FRAMINGS]  # type: ignore[valid-type]
AngleName = Literal[("",) + ANGLES]  # type: ignore[valid-type]
MoveName = Literal[("",) + MOVES]  # type: ignore[valid-type]


class _Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")


CHARACTER_KINDS = ("pawn", "vehicle", "robot", "block_animal", "block_bird", "block_fish", "prop")
DEFAULT_RADIUS_M = {"pawn": 0.35, "vehicle": 2.3, "robot": 0.55, "block_animal": 0.95, "block_bird": 0.95,
                    "block_fish": 0.65, "prop": 0.4}


class StoryCharacter(_Plan):
    """A story participant; ``kind`` picks the white-box proxy (a vehicle's radius is half its length)."""

    id: str
    role: str
    color: str
    kind: Literal["pawn", "vehicle", "robot", "block_animal", "block_bird", "block_fish", "prop"] = "pawn"
    head: Literal["sphere", "cube", "octahedron", "capsule"] = "sphere"
    body: Literal["capsule", "box", "cylinder", "cone", "taper", "ellipsoid", "bipyramid"] = "capsule"
    height_m: float = 1.7
    radius_m: float | None = Field(default=None, gt=0)
    description: str = ""

    @property
    def radius(self) -> float:
        return self.radius_m or DEFAULT_RADIUS_M[self.kind]


class StoryProp(_Plan):
    id: str
    description: str
    timeline: str = ""


class StorySpace(_Plan):
    id: str
    function: str


class RouteWaypoint(_Plan):
    x: float
    y: float
    z: float = 0.0
    at_s: float | None = Field(default=None, ge=0)
    stop_s: float = Field(default=0.0, ge=0)
    speed_mps: float | None = Field(default=None, gt=0)


class RouteSpec(_Plan):
    """Waypoints the platform compiles into keys within the actor kind's motion limits (forward/motion.py)."""

    waypoints: list[RouteWaypoint] = Field(min_length=2)
    start_s: float = Field(default=0.0, ge=0)
    start_moving: bool = False
    corner_radius_m: float | None = Field(default=None, gt=0)


class CameraRig(_Plan):
    """A camera move described by intent; the platform compiles it into keys (forward/rigs.py)."""

    type: Literal["static", "pan", "follow", "lead", "side_track", "push", "pull", "crane", "orbit", "top_down", "pov",
                  "mount"]
    target: str | None = None
    distance_m: float | None = None
    height_m: float | None = None
    lateral_m: float | None = None
    side: Literal["left", "right"] = "right"
    position: Vec3 | None = None
    arc_deg: float = 90.0
    rise_m: float = 4.0
    handheld: float = Field(default=0.0, ge=0.0, le=1.0)


class StoryShot(_Plan):
    id: str
    start_s: float
    end_s: float
    title: str
    action: str
    framing: FramingName = ""
    angle: AngleName = ""
    move: MoveName = ""
    lens_mm: float = 32.0
    camera_keys: list[Key4] = Field(default_factory=list)
    rig: CameraRig | None = None
    aim_actor: str | None = None
    aim_keys: list[Key4] = Field(default_factory=list)

    @model_validator(mode="after")
    def _camera(self) -> StoryShot:
        if not self.camera_keys and self.rig is None:
            raise ValueError(f"{self.id}: give camera_keys or a rig")
        return self


def check_event_chain(events: list[Event], twist: int, setup: list[int], duration: float) -> list[str]:
    """Rules for a story's event chain; returns human-readable problems (empty when valid)."""
    problems: list[str] = []
    if len(events) < 3:
        problems.append("events 至少 3 个（目标建立、转折、后果）")
    times = [e.at_s for e in events]
    if any(b <= a for a, b in zip(times, times[1:])):
        problems.append("events 的 at_s 必须严格递增")
    if any(t < 0 or t > duration for t in times):
        problems.append(f"events 的 at_s 必须在 0–{duration:g} 秒内")
    for i, e in enumerate(events):
        if not (e.choice or "").strip() or not (e.consequence or "").strip():
            problems.append(f"events[{i}] 需要写清行动（choice）和可见后果（consequence）")
    if not 0 <= twist < len(events):
        problems.append(f"twist={twist} 不是有效的事件序号")
    if not setup:
        problems.append("setup 不能为空：转折依据必须在前面的事件里交代")
    if len(set(setup)) != len(setup):
        problems.append("setup 序号不能重复")
    late = [i for i in setup if i >= twist]
    if late:
        problems.append(f"setup {late} 不在转折事件 {twist} 之前")
    if any(not 0 <= i < len(events) for i in setup):
        problems.append("setup 含无效的事件序号")
    return problems


class StoryPlan(_Plan):
    title: str
    logline: str
    content_class: Literal["narrative", "motion"]
    duration_s: float = Field(gt=0)
    world: str
    lighting: str = ""
    story: str
    goal: str
    obstacle: str
    stakes: str
    ending: str
    characters: list[StoryCharacter] = Field(min_length=1)
    props: list[StoryProp] = Field(default_factory=list)
    spaces: list[StorySpace] = Field(default_factory=list)
    blocks: list[Block] = Field(default_factory=list)
    paths: dict[str, list[Key4]] = Field(default_factory=dict)
    routes: dict[str, RouteSpec] = Field(default_factory=dict)
    events: list[Event] = Field(min_length=3)
    twist: int
    setup: list[int] = Field(min_length=1)
    beats: list[RouteBeat] = Field(default_factory=list)
    semantic_only: str = ""
    shots: list[StoryShot] = Field(min_length=1)
    time_map: TimeMapSpec | None = None
    dressing: bool = True
    notes: str = ""

    @model_validator(mode="after")
    def _consistency(self) -> StoryPlan:
        missing = [c.id for c in self.characters if c.id not in self.paths and c.id not in self.routes]
        if missing:
            raise ValueError(f"paths or routes missing for characters: {missing}")
        both = sorted(set(self.paths) & set(self.routes))
        if both:
            raise ValueError(f"give either a path or a route per character, not both: {both}")
        problems = check_event_chain(self.events, self.twist, self.setup, self.duration_s)
        for i, b in enumerate(self.beats):
            if not 0 <= b.start_s < b.end_s <= self.duration_s + 1e-6:
                problems.append(f"beats[{i}] 时间段无效")
        if any(b.start_s < a.start_s for a, b in zip(self.beats, self.beats[1:])):
            problems.append("beats 必须按开始时间排序")
        if problems:
            raise ValueError("；".join(problems))
        return self

    def events_with_roles(self) -> list[dict]:
        out = []
        for i, e in enumerate(self.events):
            role = "twist" if i == self.twist else ("setup" if i in self.setup else "beat")
            out.append({**e.model_dump(), "role": role})
        return out
