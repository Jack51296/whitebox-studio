"""LongTakePlan: zones traversed by one continuous camera move ([D7] 长镜头规划模式)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Zone(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    function: str
    width_m: float = Field(8.0, ge=4.0, le=30.0)
    depth_m: float = Field(10.0, ge=4.0, le=40.0)
    turn: Literal["straight", "left", "right"] = "straight"
    props: int = Field(2, ge=0, le=6)
    height_m: float = Field(3.2, ge=2.4, le=12.0)


class LongTakePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    logline: str
    duration_s: float = Field(gt=0)
    zones: list[Zone] = Field(min_length=2, max_length=12)
    with_subject: bool = True
    lens_mm: float = 22.0
    camera_height_m: float = 1.6
    door_width_m: float = Field(2.4, ge=1.6, le=6.0)
    notes: str = ""
