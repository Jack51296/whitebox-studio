"""Diversity review (team diversity-review.json): fingerprints of authored fields, exact + similar matches.

Only explicit authored fields are compared; no match never proves originality.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def _features(scene: dict) -> dict[str, set[str]]:
    control = (scene.get("source") or {}).get("control") or {}
    meta = scene.get("meta") or {}
    story = meta.get("story") or {}
    story_f = {f"content:{meta.get('labels', {}).get('content_class_label') or meta.get('content_class_label', '')}",
               f"subject:{control.get('subject', '')}/{control.get('subject_child', '')}",
               f"era:{control.get('era', '')}", f"actors:{len(scene.get('actors', []))}"}
    story_f |= {f"event:{e.get('mechanism', '')}" for e in scene.get("events", [])}
    if story.get("logline"):
        story_f.add("logline:" + story["logline"])
    photo_f = {f"shots:{len(scene['shots'])}", f"viewpoint:{control.get('viewpoint', '')}"}
    photo_f |= {f"move:{s.get('move', '')}" for s in scene["shots"]}
    photo_f |= {f"framing:{s.get('framing', '')}" for s in scene["shots"]}
    photo_f |= {f"lens:{round(s['lens_mm'] / 4) * 4}" for s in scene["shots"]}
    roles: dict[str, int] = {}
    for b in scene.get("blocks", []):
        roles[b.get("role", "")] = roles.get(b.get("role", ""), 0) + 1
    prod_f = {f"env:{meta.get('environment', '')}", f"palette:{scene.get('palette', '')}",
              f"duration:{scene['duration_s']:g}"}
    prod_f |= {f"role:{k}:{min(v, 20) // 5}" for k, v in roles.items()}
    prod_f |= {f"kind:{a['kind']}" for a in scene.get("actors", [])}
    return {"story": story_f, "photography": photo_f, "production_structure": prod_f}


def fingerprints(scene: dict) -> dict[str, str]:
    return {k: hashlib.sha256(json.dumps(sorted(v), ensure_ascii=False).encode("utf-8")).hexdigest()
            for k, v in _features(scene).items()}


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 1.0


def diversity_review(scene: dict, others: dict[str, dict], threshold: float = 0.3) -> dict[str, Any]:
    """``others`` maps job key → scene. Similar = mean Jaccard distance over the three facets ≤ threshold."""
    mine, fp = _features(scene), fingerprints(scene)
    exact: dict[str, list[str]] = {"story": [], "photography": [], "production_structure": []}
    similar = []
    for key, other in others.items():
        feats, ofp = _features(other), fingerprints(other)
        for facet in exact:
            if ofp[facet] == fp[facet]:
                exact[facet].append(key)
        distance = sum(1 - _jaccard(mine[f], feats[f]) for f in mine) / len(mine)
        if distance <= threshold:
            similar.append({"job": key, "distance": round(distance, 4)})
    similar.sort(key=lambda s: s["distance"])
    return {"fingerprints": fp, "exact_story_matches": exact["story"],
            "exact_photography_matches": exact["photography"], "exact_production_matches": exact["production_structure"],
            "similar_candidates": similar[:20], "similar_count": len(similar), "compared_against": len(others),
            "scope": "Explicit authored fields only; no semantic, geometry, rendered visibility or aesthetic verification.",
            "threshold": threshold, "no_match_does_not_prove_originality": True}
