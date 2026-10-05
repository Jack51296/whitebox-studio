from __future__ import annotations

import base64
import json

import httpx
import pytest

from wbs.errors import PaidCallNotConfirmed, WbsError
from wbs.providers import CallContext
from wbs.providers.video import CosmosTransferVideo, WanVaceComfyVideo


def _package(tmp_path, controls=("depth", "seg")):
    pkg = tmp_path / "pkg"
    (pkg / "控制通道").mkdir(parents=True)
    (pkg / "视频1_白模.mp4").write_bytes(b"WHITE")
    (pkg / "图1_主角.png").write_bytes(b"PNG")
    (pkg / "视频渲染提示词.txt").write_text("@视频1 保持调度；\"引号\"\n", encoding="utf-8")
    for name in controls:
        (pkg / "控制通道" / f"{name}.mp4").write_bytes(name.encode())
    manifest = {"video": {"file": "视频1_白模.mp4", "duration_s": 2.0}, "video_prompt_file": "视频渲染提示词.txt",
                "images": [{"ref": "@图1", "file": "图1_主角.png"}],
                "controls": {n: f"控制通道/{n}.mp4" for n in controls}}
    return pkg, manifest


def test_cosmos_sync_sends_controls_with_weights(tmp_path, monkeypatch):
    monkeypatch.setenv("WBS_COSMOS_ENDPOINT", "http://nim.local")
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"video": base64.b64encode(b"RESULT").decode()})

    pkg, manifest = _package(tmp_path)
    provider = CosmosTransferVideo({"control_weights": {"depth": 0.6, "seg": 0.2}, "seed": 7}, httpx.MockTransport(handler))
    result = provider.submit(package_dir=pkg, manifest=manifest)
    assert result.status == "completed" and (pkg / "成片_cosmos_transfer.mp4").read_bytes() == b"RESULT"
    body = seen["body"]
    assert seen["url"] == "http://nim.local/v1/infer" and body["seed"] == 7
    assert body["controls"]["depth"]["control_weight"] == 0.6 and base64.b64decode(body["controls"]["seg"]["video"]) == b"seg"
    assert base64.b64decode(body["video"]) == b"WHITE" and "引号" in body["prompt"]


def test_cosmos_polls_async_jobs_and_requires_controls(tmp_path, monkeypatch):
    monkeypatch.setenv("WBS_COSMOS_ENDPOINT", "http://nim.local")
    states = iter([{"status": "running"}, {"status": "done", "video": base64.b64encode(b"LATE").decode()}])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(202, json={"id": "job-1"})
        assert request.url.path == "/v1/infer/job-1"
        return httpx.Response(200, json=next(states))

    pkg, manifest = _package(tmp_path)
    result = CosmosTransferVideo({"poll_s": 0}, httpx.MockTransport(handler)).submit(package_dir=pkg, manifest=manifest)
    assert result.remote_id == "job-1" and (pkg / "成片_cosmos_transfer.mp4").read_bytes() == b"LATE"
    bare, manifest = _package(tmp_path / "b", controls=())
    with pytest.raises(WbsError, match="控制通道"):
        CosmosTransferVideo({}, httpx.MockTransport(handler)).submit(package_dir=bare, manifest=manifest)


def test_wan_vace_comfyui_round_trip(tmp_path):
    workflow = tmp_path / "wf.json"
    workflow.write_text(json.dumps({"3": {"inputs": {"text": "{{PROMPT}}"}}, "5": {"inputs": {"video": "{{CONTROL_VIDEO}}"}},
                                    "6": {"inputs": {"image": "{{REFERENCE_IMAGE}}"}}}), encoding="utf-8")
    calls, polls = [], iter([{}, {"p1": {"outputs": {"9": {"gifs": [{"filename": "wan.mp4", "subfolder": "", "type": "output"}]}}}}])

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/upload/image":
            name = "depth.mp4" if b"depth.mp4" in request.content else "ref.png"
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        if request.url.path == "/prompt":
            graph = json.loads(request.content)["prompt"]
            assert graph["5"]["inputs"]["video"] == "depth.mp4" and graph["6"]["inputs"]["image"] == "ref.png"
            assert graph["3"]["inputs"]["text"].startswith("@视频1") and '"引号"' in graph["3"]["inputs"]["text"]
            return httpx.Response(200, json={"prompt_id": "p1"})
        if request.url.path == "/history/p1":
            return httpx.Response(200, json=next(polls))
        assert request.url.path == "/view" and request.url.params["filename"] == "wan.mp4"
        return httpx.Response(200, content=b"WANVIDEO")

    pkg, manifest = _package(tmp_path)
    provider = WanVaceComfyVideo({"workflow": str(workflow), "poll_s": 0}, httpx.MockTransport(handler))
    result = provider.submit(package_dir=pkg, manifest=manifest)
    assert result.status == "completed" and (pkg / "成片_wan_vace.mp4").read_bytes() == b"WANVIDEO"
    assert calls.count("/upload/image") == 2 and calls[-1] == "/view"


def test_self_hosted_backends_stay_behind_the_paid_guard(tmp_path, monkeypatch, workspace):
    from wbs.config import load_settings
    from wbs.ledger import Ledger
    from wbs.providers import CallPolicy, GuardedVideo, _Guard

    monkeypatch.setenv("WBS_COSMOS_ENDPOINT", "http://nim.local")
    provider = CosmosTransferVideo({}, httpx.MockTransport(lambda r: httpx.Response(500)))
    pkg, manifest = _package(tmp_path)
    settings = load_settings()
    guard = _Guard(Ledger(tmp_path / "l.sqlite"), CallPolicy(False, False, settings.cost.budget.per_job_cny, 2000))
    with pytest.raises(PaidCallNotConfirmed):
        GuardedVideo(provider, guard).submit(CallContext("b/j", "v2v_submit"), package_dir=pkg, manifest=manifest, video_seconds=2)
    dry = _Guard(Ledger(tmp_path / "l2.sqlite"), CallPolicy(False, True, 50, 2000))
    assert GuardedVideo(provider, dry).submit(CallContext("b/j", "v2v_submit"), package_dir=pkg, manifest=manifest,
                                              video_seconds=2).status == "dry_run"
    with pytest.raises(WbsError, match="工作流"):
        WanVaceComfyVideo({"workflow": str(tmp_path / "missing.json")})
