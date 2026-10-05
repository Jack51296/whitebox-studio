"""Download pinned public model files with resume, parallel ranges and sha256 verification."""

from __future__ import annotations

import hashlib
import os
import shutil
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx

from ..config import REPO_ROOT, load_settings
from ..jsonio import read_json, write_json
from .registry import MODELS, ModelFile, ModelSpec

DEFAULT_ENDPOINT = "https://huggingface.co"
MIRRORS = {
    "huggingface": "https://huggingface.co/{repo}/resolve/{revision}/{path}",
    "hf-mirror": "https://hf-mirror.com/{repo}/resolve/{revision}/{path}",
    "modelscope": "https://modelscope.cn/models/{repo}/resolve/master/{path}",
}
CHUNK = 1 << 20
PARALLEL_MIN = 64 << 20


def models_dir() -> Path:
    env = os.environ.get("WBS_MODELS_DIR")
    path = Path(env or load_settings().vision.models_dir)
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def model_path(name: str) -> Path:
    return models_dir() / name


def local_name(f: ModelFile) -> str:
    return Path(f.path).name


def file_url(spec: ModelSpec, f: ModelFile, endpoint: str = DEFAULT_ENDPOINT) -> str:
    """``endpoint`` is a Hugging Face compatible base URL or a MIRRORS key; the pinned sha256 is what counts."""
    if spec.source != "hf":
        return f"{spec.repo}/{spec.revision}/{f.path}"
    template = MIRRORS.get(endpoint) or endpoint.rstrip("/") + "/{repo}/resolve/{revision}/{path}"
    return template.format(repo=spec.repo, revision=spec.revision, path=f.path)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK * 4), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def _manifest(root: Path) -> dict[str, Any]:
    path = _manifest_path(root)
    return read_json(path) if path.exists() else {"schema": "wbs.models/1.0", "models": {}}


def _stamp(path: Path) -> list[float]:
    st = path.stat()
    return [st.st_size, round(st.st_mtime, 3)]


def verify(spec: ModelSpec, root: Path | None = None, *, rehash: bool = False) -> dict[str, Any]:
    """Size check always; sha256 only when the file changed since it was last verified (or ``rehash``)."""
    root = root or models_dir()
    entry = _manifest(root)["models"].get(spec.name, {})
    files, ok = [], True
    for f in spec.files:
        path = root / spec.name / local_name(f)
        row: dict[str, Any] = {"file": local_name(f), "present": path.exists()}
        if not path.exists() or path.stat().st_size != f.size:
            row["status"] = "missing" if not path.exists() else "size_mismatch"
            ok = False
        else:
            known = entry.get("files", {}).get(local_name(f), {})
            if not rehash and known.get("stamp") == _stamp(path) and known.get("sha256") == (f.sha256 or known.get("sha256")):
                row["status"] = "verified"
            else:
                actual = sha256_of(path)
                row["status"] = "verified" if not f.sha256 or actual == f.sha256 else "sha256_mismatch"
                row["sha256"] = actual
            ok &= row["status"] == "verified"
        files.append(row)
    return {"name": spec.name, "ok": ok and bool(spec.files), "files": files}


def _download_range(client: httpx.Client, url: str, dest: Path, start: int, end: int, retries: int = 5) -> None:
    for attempt in range(retries):
        have = dest.stat().st_size if dest.exists() else 0
        if start + have > end:
            return
        try:
            with client.stream("GET", url, headers={"Range": f"bytes={start + have}-{end}"}) as r:
                if r.status_code not in (200, 206):
                    raise httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                if r.status_code == 200 and start + have > 0:
                    raise httpx.HTTPError("server ignored Range header")
                with dest.open("ab") as out:
                    for block in r.iter_bytes(CHUNK):
                        out.write(block)
            if dest.stat().st_size >= end - start + 1:
                return
        except httpx.HTTPError:
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))


def download(url: str, dest: Path, size: int, *, parts: int = 8, timeout: float = 60.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        if size < PARALLEL_MIN or parts <= 1:
            _download_range(client, url, dest.with_suffix(dest.suffix + ".part"), 0, size - 1)
            dest.with_suffix(dest.suffix + ".part").replace(dest)
            return
        step = -(-size // parts)
        ranges = [(i * step, min(size, (i + 1) * step) - 1) for i in range(parts)]
        pieces = [dest.with_suffix(dest.suffix + f".part{i}") for i in range(parts)]
        with ThreadPoolExecutor(parts) as pool:
            for future in [pool.submit(_download_range, client, url, p, a, b) for p, (a, b) in zip(pieces, ranges)]:
                future.result()
        with dest.open("wb") as out:
            for piece in pieces:
                with piece.open("rb") as src:
                    shutil.copyfileobj(src, out, CHUNK * 8)
        for piece in pieces:
            piece.unlink()


def fetch(spec: ModelSpec, root: Path | None = None, *, endpoint: str = DEFAULT_ENDPOINT, dry_run: bool = False,
          allow_unpinned: bool = False, log: Callable[[str], None] = print) -> dict[str, Any]:
    root = root or models_dir()
    if spec.gated:
        return {"name": spec.name, "status": "skipped_gated", "note": spec.note}
    if not spec.commercial:
        return {"name": spec.name, "status": "skipped_license", "note": spec.license}
    unpinned = [f.path for f in spec.files if not f.sha256]
    if unpinned and not allow_unpinned:
        return {"name": spec.name, "status": "refused_unpinned", "files": unpinned}
    if dry_run:
        return {"name": spec.name, "status": "planned", "bytes": spec.size,
                "urls": [file_url(spec, f, endpoint) for f in spec.files]}
    manifest = _manifest(root)
    rows = {}
    for f in spec.files:
        dest = root / spec.name / local_name(f)
        if dest.exists() and dest.stat().st_size == f.size and (not f.sha256 or sha256_of(dest) == f.sha256):
            log(f"[{spec.name}] {local_name(f)} 已存在，校验通过")
        else:
            if dest.exists():
                dest.unlink()
            started = time.time()
            log(f"[{spec.name}] 下载 {local_name(f)} ({f.size / 1e6:.1f} MB)")
            download(file_url(spec, f, endpoint), dest, f.size)
            log(f"[{spec.name}] {local_name(f)} 完成，{f.size / 1e6 / max(time.time() - started, 1e-3):.2f} MB/s")
        actual = sha256_of(dest)
        if f.sha256 and actual != f.sha256:
            dest.unlink()
            raise ValueError(f"{spec.name}/{local_name(f)} sha256 不符：期望 {f.sha256}，实际 {actual}（已删除）")
        rows[local_name(f)] = {"sha256": actual, "size": f.size, "stamp": _stamp(dest), "pinned": bool(f.sha256)}
    manifest["models"][spec.name] = {"repo": spec.repo, "revision": spec.revision, "license": spec.license,
                                     "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "files": rows}
    write_json(_manifest_path(root), manifest)
    return {"name": spec.name, "status": "ok", "files": rows}


def status(root: Path | None = None) -> dict[str, Any]:
    root = root or models_dir()
    out = {}
    for spec in MODELS:
        row: dict[str, Any] = {"license": spec.license, "purpose": spec.purpose, "profiles": list(spec.profiles),
                               "gated": spec.gated}
        if spec.gated:
            present = (root / spec.name).exists() and any((root / spec.name).iterdir())
            row.update(present=present, note=spec.note)
        else:
            check = verify(spec, root)
            row.update(present=check["ok"], files=[f"{f['file']}:{f['status']}" for f in check["files"]])
        out[spec.name] = row
    return out
