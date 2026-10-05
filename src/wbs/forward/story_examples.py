"""Runtime director cards for few-shot planning and descriptive comparison reports.

The release uses the same parsed fields as the baseline without original delivery archives.
"""

from __future__ import annotations

import re
import statistics
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from typing import Any

from ..config import REPO_ROOT
from ..jsonio import read_json

SHOT_LINE = re.compile(r"^[【\[]?(?:S\d+[A-Z]?[^｜|\n]*|镜\d+)[｜|]\s*([\d.]+)\s*[–\-~]\s*([\d.]+)\s*(?:s|秒)", re.M)
EVENT_LINE = re.compile(r"^(?:E\d+|e\d+)\b.*$", re.M)
STRUCTURE_KEYS = ("目标", "策略", "阻力", "代价", "后果", "转折前", "转折后", "转折", "结局", "结束", "依据", "铺垫",
                  "升级", "高潮与转折", "转折依据与信息差", "结束状态", "结局余味")
HEADING = re.compile(r"^[一二三四五六七八九十]+、", re.M)
SECTIONS = ("一句话", "完整剧本", "世界与空间", "人物", "摄影意图", "逐镜", "白模实际锁定", "仅在剧本", "声音", "验收",
            "路线与空间交接")


@dataclass
class TeamCard:
    name: str
    path: str
    content_class: str | None
    duration_s: float | None
    shots: int
    shot_lengths: list[float]
    logline: str
    story: str
    structure: list[str] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)

    @property
    def completeness(self) -> int:
        return (bool(self.logline) + bool(self.story) + bool(self.events) + bool(self.structure)
                + (self.shots > 0))

    def as_example(self, story_chars: int = 360, max_lines: int = 8) -> dict[str, Any]:
        story = self.story if len(self.story) <= story_chars else self.story[:story_chars].rstrip() + "…"
        return {"title": self.name, "content_class": self.content_class or "", "duration_s": self.duration_s,
                "shots": self.shots, "logline": self.logline, "story": story,
                "structure": self.structure[:max_lines], "events": self.events[:max_lines]}


def _after(text: str, markers: tuple[str, ...]) -> str:
    for marker in markers:
        m = re.search(rf"^{marker}[：:]\s*(.+)$", text, re.M)
        if m:
            return m.group(1).strip()
    return ""


def _section(text: str, heading: str) -> str:
    """Body of the first numbered section (一、二、…) whose heading mentions ``heading``."""
    for m in HEADING.finditer(text):
        line_end = text.find("\n", m.start())
        title = text[m.start():line_end if line_end >= 0 else len(text)]
        if heading in title:
            nxt = HEADING.search(text, line_end + 1 if line_end >= 0 else len(text))
            return text[line_end + 1:nxt.start() if nxt else len(text)].strip()
    return ""


def _paragraphs(body: str) -> list[str]:
    return [" ".join(ln.strip() for ln in p.splitlines() if ln.strip()) for p in re.split(r"\n\s*\n", body) if p.strip()]


def _is_structure(line: str) -> bool:
    return bool(re.match(rf"^({'|'.join(STRUCTURE_KEYS)})[：:/]", line.strip()))


def _events(lines: list[str]) -> list[str]:
    out = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not re.match(r"^(?:E\d+|e\d+)\b", s):
            continue
        body = re.sub(r"^(?:E\d+|e\d+)\S*?[｜|：:\s]*", "", s)
        if len(body) < 14:
            action = consequence = ""
            for nxt in lines[i + 1:i + 8]:
                n = nxt.strip()
                if re.match(r"^(?:E\d+|e\d+)\b", n):
                    break
                if n.startswith("完整行动："):
                    action = n.split("：", 1)[1]
                elif n.startswith("后果："):
                    consequence = n.split("：", 1)[1]
            if action:
                s = f"{s}｜{action}" + (f" → {consequence}" if consequence else "")
        out.append(s)
    return out


def parse_card(text: str, name: str = "", content_class: str | None = None) -> TeamCard:
    lines = text.splitlines()
    logline = _after(text, ("一句话", "一句话故事"))
    if not logline:
        first = [ln.strip() for ln in _section(text, "一句话").splitlines() if ln.strip() and not _is_structure(ln)]
        logline = first[0] if first else ""
    story = _after(text, ("完整剧本",))
    if not story:
        paras = [p for p in _paragraphs(_section(text, "完整剧本"))
                 if p != logline and not _is_structure(p) and not p.startswith("【")]
        story = max(paras, key=len) if paras else ""
    structure = [ln.strip() for ln in lines if _is_structure(ln)]
    events = _events(lines)
    spans = [(float(a), float(b)) for a, b in SHOT_LINE.findall(text)]
    lengths = [round(b - a, 3) for a, b in spans if b > a]
    duration = None
    for pattern in (r"(\d+(?:\.\d+)?)\s*秒[｜|]", r"(\d+(?:\.\d+)?)\s*秒，\d+fps", r"｜\s*(\d+(?:\.\d+)?)\s*秒\s*｜",
                    r"(\d+(?:\.\d+)?)\s*秒"):
        m = re.search(pattern, "\n".join(lines[:4]))
        if m:
            duration = float(m.group(1))
            break
    if duration is None and spans:
        duration = max(b for _, b in spans)
    shots = len(spans)
    m = re.search(r"(\d+)\s*(?:个物理镜头|镜)", "\n".join(lines[:4]))
    if m and not shots:
        shots = int(m.group(1))
    if content_class is None:
        head = "\n".join(lines[:15])
        if re.search(r"运动类|类别 motion|\bmotion\b", head):
            content_class = "motion"
        elif re.search(r"叙事类|类别 narrative|\bnarrative\b", head):
            content_class = "narrative"
    return TeamCard(name=name, path="", content_class=content_class, duration_s=duration, shots=shots,
                    shot_lengths=lengths, logline=logline, story=story, structure=structure, events=events,
                    sections=[s for s in SECTIONS if s in text])


@lru_cache(maxsize=1)
def load_team_cards() -> tuple[TeamCard, ...]:
    runtime = REPO_ROOT / "data" / "story-cards.json"
    if runtime.exists():
        data = read_json(runtime)
        if data.get("schema") != "wbs.story_cards/1.0":
            raise ValueError("unsupported runtime story-card schema")
        return tuple(TeamCard(**item) for item in data["cards"])
    return ()


def pick_examples(per_class: int = 2) -> list[TeamCard]:
    """Most complete cards, ``per_class`` narrative + ``per_class`` motion, deterministic order."""
    chosen = []
    for cls in ("narrative", "motion"):
        pool = [c for c in load_team_cards() if c.content_class == cls and c.logline and c.story]
        pool.sort(key=lambda c: (-c.completeness, c.name))
        chosen.extend(pool[:per_class])
    return chosen


def _range(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {"min": round(min(values), 3), "median": round(statistics.median(values), 3), "max": round(max(values), 3),
            "n": len(values)}


def reference_stats(content_class: str | None = None) -> dict[str, Any]:
    cards = [c for c in load_team_cards() if content_class is None or c.content_class == content_class]
    return {
        "cards": len(cards), "content_class": content_class or "all",
        "duration_s": _range([c.duration_s for c in cards if c.duration_s]),
        "shots": _range([c.shots for c in cards if c.shots]),
        "shot_length_s": _range([x for c in cards for x in c.shot_lengths]),
        "median_shot_length_s": _range([statistics.median(c.shot_lengths) for c in cards if c.shot_lengths]),
        "events": _range([len(c.events) for c in cards if c.events]),
        "story_chars": _range([len(c.story) for c in cards if c.story]),
    }


def compare_card(card_text: str, content_class: str | None) -> dict[str, Any]:
    """Descriptive comparison of a generated director card with the team cards (no pass/fail)."""
    mine = parse_card(card_text, "generated", content_class)
    ref = reference_stats(content_class if content_class in ("narrative", "motion") else None)
    notes = []

    def outside(label: str, value: float | None, rng: dict | None) -> None:
        if value is None or rng is None:
            return
        if value < rng["min"] or value > rng["max"]:
            notes.append(f"{label} {value:g} 在团队样例范围 {rng['min']:g}–{rng['max']:g} 之外（中位 {rng['median']:g}）")

    outside("镜头数", mine.shots, ref["shots"])
    if mine.shot_lengths:
        outside("镜长中位数（秒）", statistics.median(mine.shot_lengths), ref["median_shot_length_s"])
        outside("最长镜头（秒）", max(mine.shot_lengths), ref["shot_length_s"])
    outside("事件数", len(mine.events), ref["events"])
    outside("完整剧本字数", len(mine.story), ref["story_chars"])
    missing = [s for s in ("一句话", "完整剧本", "摄影意图", "路线与空间交接") if s not in mine.sections]
    if missing:
        notes.append("缺少团队卡常见段落：" + "、".join(missing))
    if not any("（转折）" in e for e in mine.events):
        notes.append("事件链没有标出转折事件")
    if not ref["cards"]:
        notes.append("没有找到团队样例（references/samples 缺失），只检查了段落与转折标记")
    return {"schema": "wbs.card_compare/1.0", "status": "informational",
            "scope": "只比较可统计的结构特征（镜头数、镜长、事件数、剧本长度、段落），不评价故事好坏",
            "generated": {k: v for k, v in asdict(mine).items() if k not in ("story", "path", "name")}
            | {"story_chars": len(mine.story)},
            "team_reference": ref, "notes": notes}
