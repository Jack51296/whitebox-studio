"""Video submission adapters. Default is export-only, per Skill v3 (submit only when explicitly asked).

Optional self-hosted backends (still off by default; the Skill v3 package and its @视频1 / @图N contract stay the same):
- ``cosmos_transfer``: NVIDIA Cosmos-Transfer2.5 in a NIM container (NVIDIA Open Model License; Hopper-class, ~80 GB);
- ``wan_vace``: Wan2.2 VACE-Fun (Apache-2.0) through a local ComfyUI server (480p experiments on a 12 GB card).
Both take the white-box video plus the control passes written by ``wbs render --passes depth,seg,edge``.
"""

from __future__ import annotations

import base64
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from ..config import config_file
from ..errors import ProviderNotConfigured, TransientError, WbsError
from ..jsonio import write_text
from .base import SubmitResult, VideoProvider


class ManualVideo(VideoProvider):
    """Writes upload instructions next to the package; nothing leaves the machine."""

    name = "manual"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.model = cfg.get("model")

    def submit(self, *, package_dir: Path, manifest: dict[str, Any]) -> SubmitResult:
        refs = [f"- {item['ref']}：{item['file']}" for item in manifest.get("images", []) if item.get("file")]
        text = "\n".join([
            "# 提交说明（手工）", "",
            f"目标视频模型：{self.model or '未指定'}。本包未自动上传任何平台。", "",
            "上传顺序与引用：",
            f"- @视频1：{manifest.get('video', {}).get('file', '')}",
            *refs,
            "- 视频提示词：视频渲染提示词.txt（整段复制，保持 @视频1 / @图N 引用不变）", "",
            "Skill v3 规定：模型可能失败，按平台规则重试；不要把失败的图片改作他用或重新编号。",
        ]) + "\n"
        write_text(package_dir / "提交说明.md", text)
        return SubmitResult(status="exported", message="已生成手工提交说明，未上传")


class HttpGenericVideo(VideoProvider):
    """Generic multipart POST adapter; adapt field names once the platform's API spec is available."""

    name = "http_generic"
    is_paid = True

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.endpoint = os.environ.get(cfg.get("endpoint_env") or "", "")
        self._key = os.environ.get(cfg.get("api_key_env") or "", "")
        self.model = cfg.get("model")
        self.timeout = float(cfg.get("timeout_s", 600))
        if not self.endpoint or not self._key:
            raise ProviderNotConfigured(
                f"视频接口未配置：请设置 {cfg.get('endpoint_env')} 与 {cfg.get('api_key_env')}；"
                "sd1080p-duofight 等平台的接口说明缺失，接入前需按其规范调整字段")

    def submit(self, *, package_dir: Path, manifest: dict[str, Any]) -> SubmitResult:
        video = package_dir / manifest["video"]["file"]
        files = [("video", (video.name, video.read_bytes(), "video/mp4"))]
        for item in manifest.get("images", []):
            if item.get("file"):
                path = package_dir / item["file"]
                files.append(("images", (path.name, path.read_bytes(), "image/png")))
        prompt = (package_dir / "视频渲染提示词.txt").read_text(encoding="utf-8")
        try:
            response = httpx.post(self.endpoint, headers={"Authorization": f"Bearer {self._key}"},
                                  data={"model": self.model or "", "prompt": prompt}, files=files,
                                  timeout=self.timeout)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise TransientError(str(exc)) from exc
        if response.status_code in (429, 500, 502, 503, 504):
            raise TransientError(f"HTTP {response.status_code}")
        if response.is_error:
            raise WbsError(f"HTTP {response.status_code}: {response.text[:300]}")
        data = response.json()
        return SubmitResult(status="submitted", message="已提交", remote_id=str(data.get("id") or ""), details=data)


def _check(response: httpx.Response) -> httpx.Response:
    if response.status_code in (429, 500, 502, 503, 504):
        raise TransientError(f"HTTP {response.status_code}")
    if response.is_error:
        raise WbsError(f"HTTP {response.status_code}: {response.text[:300]}")
    return response


def _b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


class CosmosTransferVideo(VideoProvider):
    """White-box video + depth / seg / edge controls → Cosmos-Transfer NIM. Field names follow the deployed NIM
    version (``infer_path`` / ``status_path`` configurable); verify them against the server before first use."""

    name = "cosmos_transfer"
    is_paid = True

    def __init__(self, cfg: dict[str, Any], transport: httpx.BaseTransport | None = None) -> None:
        self.endpoint = os.environ.get(cfg.get("endpoint_env") or "WBS_COSMOS_ENDPOINT", "").rstrip("/")
        self._key = os.environ.get(cfg.get("api_key_env") or "WBS_COSMOS_API_KEY", "")
        self.model = cfg.get("model") or "cosmos-transfer2.5"
        self.weights = cfg.get("control_weights") or {"depth": 0.5, "seg": 0.3, "edge": 0.4}
        self.infer_path = cfg.get("infer_path", "/v1/infer")
        self.status_path = cfg.get("status_path", "/v1/infer/{id}")
        self.params = {k: cfg[k] for k in ("seed", "num_steps", "guidance", "negative_prompt") if k in cfg}
        self.timeout, self.poll_s = float(cfg.get("timeout_s", 1800)), float(cfg.get("poll_s", 10))
        self._transport = transport
        if not self.endpoint:
            raise ProviderNotConfigured(f"Cosmos-Transfer 未配置：请设置 {cfg.get('endpoint_env') or 'WBS_COSMOS_ENDPOINT'}"
                                        "（自建 NIM 服务地址）")

    def _client(self) -> httpx.Client:
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        return httpx.Client(timeout=120, headers=headers, transport=self._transport)

    def submit(self, *, package_dir: Path, manifest: dict[str, Any]) -> SubmitResult:
        controls = {name: {"video": _b64(package_dir / rel), "control_weight": float(self.weights.get(name, 0.3))}
                    for name, rel in (manifest.get("controls") or {}).items() if name in self.weights}
        if not controls:
            raise WbsError("提交包里没有控制通道：先运行 wbs render <任务> --passes depth,seg,edge，再重建提交包")
        body = {"model": self.model, "prompt": (package_dir / manifest["video_prompt_file"]).read_text(encoding="utf-8"),
                "video": _b64(package_dir / manifest["video"]["file"]), "controls": controls, **self.params}
        started = time.time()
        with self._client() as client:
            try:
                data = _check(client.post(self.endpoint + self.infer_path, json=body)).json()
                job_id = data.get("id") or data.get("request_id")
                while "video" not in data and job_id:
                    if time.time() - started > self.timeout:
                        raise WbsError(f"Cosmos-Transfer 任务 {job_id} 超时")
                    time.sleep(self.poll_s)
                    data = _check(client.get(self.endpoint + self.status_path.format(id=job_id))).json()
                    if data.get("status") in ("failed", "error"):
                        raise WbsError(f"Cosmos-Transfer 任务失败：{data.get('error') or data}")
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TransientError(str(exc)) from exc
        if "video" not in data:
            raise WbsError(f"Cosmos-Transfer 未返回视频：{str(data)[:300]}")
        out = package_dir / "成片_cosmos_transfer.mp4"
        out.write_bytes(base64.b64decode(data["video"]))
        return SubmitResult(status="completed", message="Cosmos-Transfer 已返回成片", remote_id=str(job_id or ""),
                            details={"output": out.name, "controls": sorted(controls), "seconds": round(time.time() - started, 1)})


class WanVaceComfyVideo(VideoProvider):
    """Local ComfyUI: upload control video / reference image, queue an API-format workflow, download the result.

    The workflow file (ComfyUI "Save (API)" export) uses the placeholders ``"{{PROMPT}}"``, ``"{{CONTROL_VIDEO}}"``
    and ``"{{REFERENCE_IMAGE}}"``; weights (Wan2.2 VACE-Fun GGUF, LightX2V) are installed in ComfyUI by the user.
    """

    name = "wan_vace"
    is_paid = False

    def __init__(self, cfg: dict[str, Any], transport: httpx.BaseTransport | None = None) -> None:
        self.server = (os.environ.get(cfg.get("server_env") or "WBS_COMFYUI_URL") or cfg.get("server") or
                       "http://127.0.0.1:8188").rstrip("/")
        self.workflow = config_file(cfg.get("workflow") or "comfyui/wan_vace_480p.api.json")
        self.control = cfg.get("control", "depth")
        self.model = cfg.get("model") or "Wan2.2-VACE-Fun-A14B"
        self.timeout, self.poll_s = float(cfg.get("timeout_s", 3600)), float(cfg.get("poll_s", 5))
        self._transport = transport
        if not self.workflow.exists():
            raise ProviderNotConfigured(f"缺少 ComfyUI 工作流 {self.workflow}（在 ComfyUI 里用 Save (API) 导出，"
                                        "把提示词、控制视频、参考图输入改成 {{PROMPT}} / {{CONTROL_VIDEO}} / {{REFERENCE_IMAGE}}）")

    def _upload(self, client: httpx.Client, path: Path) -> str:
        files = {"image": (path.name, path.read_bytes(), "application/octet-stream")}
        data = _check(client.post(f"{self.server}/upload/image", files=files, data={"type": "input", "overwrite": "true"})).json()
        return f"{data['subfolder']}/{data['name']}" if data.get("subfolder") else data["name"]

    def submit(self, *, package_dir: Path, manifest: dict[str, Any]) -> SubmitResult:
        controls = manifest.get("controls") or {}
        control = package_dir / controls.get(self.control, manifest["video"]["file"])
        reference = next((package_dir / i["file"] for i in manifest.get("images", []) if i.get("file")), None)
        prompt = (package_dir / manifest["video_prompt_file"]).read_text(encoding="utf-8").strip()
        started = time.time()
        with httpx.Client(timeout=120, transport=self._transport) as client:
            try:
                text = self.workflow.read_text(encoding="utf-8")
                text = text.replace('"{{PROMPT}}"', json.dumps(prompt, ensure_ascii=False))
                text = text.replace("{{CONTROL_VIDEO}}", self._upload(client, control))
                if reference is not None:
                    text = text.replace("{{REFERENCE_IMAGE}}", self._upload(client, reference))
                queued = _check(client.post(f"{self.server}/prompt",
                                            json={"prompt": json.loads(text), "client_id": uuid.uuid4().hex})).json()
                prompt_id = queued["prompt_id"]
                while True:
                    history = _check(client.get(f"{self.server}/history/{prompt_id}")).json().get(prompt_id)
                    if history and history.get("outputs"):
                        break
                    if time.time() - started > self.timeout:
                        raise WbsError(f"ComfyUI 任务 {prompt_id} 超时")
                    time.sleep(self.poll_s)
                files = [f for node in history["outputs"].values() for key in ("gifs", "videos", "images")
                         for f in node.get(key, []) if str(f.get("filename", "")).endswith((".mp4", ".webm", ".mov"))]
                if not files:
                    raise WbsError(f"ComfyUI 输出里没有视频：{list(history['outputs'])}")
                video = _check(client.get(f"{self.server}/view", params={"filename": files[0]["filename"],
                                                                         "subfolder": files[0].get("subfolder", ""),
                                                                         "type": files[0].get("type", "output")})).content
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise TransientError(str(exc)) from exc
        out = package_dir / "成片_wan_vace.mp4"
        out.write_bytes(video)
        return SubmitResult(status="completed", message="Wan VACE（本机 ComfyUI）已返回成片", remote_id=prompt_id,
                            details={"output": out.name, "control": control.name, "seconds": round(time.time() - started, 1)})
