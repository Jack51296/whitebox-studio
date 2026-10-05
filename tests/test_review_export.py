from __future__ import annotations

import pytest

from wbs.config import load_settings
from wbs.jsonio import read_json, write_json
from wbs.layout import Workspace
from wbs.ledger import Ledger
from wbs.qc import review_export as rx


def test_manifest_export_and_tag_sync_need_a_reviewer(workspace):
    ws = Workspace(load_settings().workspace).ensure()
    ledger = Ledger(ws.ledger_path)
    job = ws.job("RB", "RB_0001")
    job.root.mkdir(parents=True)
    write_json(job.scene, {"title": "片"})
    job.update_meta(title="片", qc_status="passed")
    job.video("片").write_bytes(b"mp4")
    out = rx.export(ws, "RB", ledger)
    assert out["backend"] in ("manifest", "fiftyone") and out["groups"] == 1
    doc = read_json(rx.manifest_path(ws, "RB"))
    group = doc["groups"][0]
    assert group["slices"]["whitebox"].endswith("片_白模参考.mp4") and group["slices"]["v2v"] is None
    if doc["backend"] != "manifest":
        pytest.skip("FiftyOne installed: tags are read from the dataset")
    group["tags"] = ["sampled_visual:passed", "adopted", "looks-great"]
    write_json(rx.manifest_path(ws, "RB"), doc)
    with pytest.raises(ValueError):
        rx.sync(ws, "RB", ledger, " ")
    result = rx.sync(ws, "RB", ledger, "张三")
    assert {(a["field"], a["value"]) for a in result["applied"]} == {("sampled_visual", "passed"), ("curation", "adopted")}
    assert result["ignored"] == [{"job": "RB/RB_0001", "tag": "looks-great"}]
    reviews = ledger.reviews("RB/RB_0001")
    assert reviews["sampled_visual"]["value"] == "passed" and reviews["curation"]["reviewer"] == "张三"
    assert rx.sync(ws, "RB", ledger, "张三")["applied"] == []
