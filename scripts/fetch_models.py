"""Download the optional vision model weights: public URLs only, pinned revision, sha256 verified.

  python scripts/fetch_models.py --list
  python scripts/fetch_models.py --profile default            # ≈1 GB: TransNetV2, CenterFace, Grounding DINO tiny, SAM 2.1 tiny, DA3-SMALL
  python scripts/fetch_models.py --profile full               # + DA3-BASE, DA3METRIC-LARGE
  python scripts/fetch_models.py --models map-anything-apache # 4.9 GB, needs the mapanything package in .venv-vision
  python scripts/fetch_models.py --profile default --endpoint modelscope   # public mirror, same files (sha256 checked)

Gated repositories (SAM 3, VGGT-1B-Commercial) are listed and skipped: access is never requested.
``--check-blobs`` (maintainers) compares small unpinned files with the git blob ids published by the
server before their sha256 is pinned in src/wbs/vision/registry.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from wbs.vision import registry  # noqa: E402
from wbs.vision.fetch import DEFAULT_ENDPOINT, fetch, local_name, models_dir, verify  # noqa: E402


def git_blob_sha1(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def server_blob_ids(spec: registry.ModelSpec) -> dict[str, str]:
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        if spec.source == "hf":
            info = client.get(f"https://huggingface.co/api/models/{spec.repo}/revision/{spec.revision}",
                              params={"blobs": "true"}).json()
            return {s["rfilename"]: s.get("blobId", "") for s in info.get("siblings", [])}
        owner_repo = spec.repo.split("githubusercontent.com/", 1)[1]
        out = {}
        for f in spec.files:
            r = client.get(f"https://api.github.com/repos/{owner_repo}/contents/{f.path}", params={"ref": spec.revision})
            out[f.path] = r.json().get("sha", "")
        return out


def check_blobs(specs: list[registry.ModelSpec]) -> int:
    bad = 0
    for spec in specs:
        unpinned = [f for f in spec.files if not f.sha256]
        if not unpinned:
            continue
        ids = server_blob_ids(spec)
        for f in unpinned:
            path = models_dir() / spec.name / local_name(f)
            if not path.exists():
                print(f"MISSING {spec.name}/{f.path}")
                bad += 1
                continue
            ok = ids.get(f.path) == git_blob_sha1(path)
            bad += not ok
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            print(f"{'OK ' if ok else 'BAD'} {spec.name}/{f.path} blob={ids.get(f.path)} sha256={sha}")
    return bad


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--profile", choices=["default", "full", "mapanything"])
    parser.add_argument("--models", help="comma separated model names")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                        help="huggingface | hf-mirror | modelscope, or any Hugging Face compatible URL (files must match the pinned sha256)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-unpinned", action="store_true", help="maintainers: download files whose sha256 is not pinned yet")
    parser.add_argument("--check-blobs", action="store_true")
    args = parser.parse_args()

    if args.list:
        for m in registry.MODELS:
            flag = "门控-不下载" if m.gated else ",".join(m.profiles) or "-"
            print(f"{m.name:22} {m.size / 1e6:9.1f} MB  [{flag}]  {m.license}  {m.purpose}")
        print("\n不进入生产路径（许可证限制）：")
        for name, why in registry.EXCLUDED:
            print(f"  {name}: {why}")
        return
    names = [n.strip() for n in (args.models or "").split(",") if n.strip()]
    specs = [registry.get(n) for n in names] if names else registry.profile(args.profile or "default")
    if args.check_blobs:
        raise SystemExit(1 if check_blobs(specs) else 0)
    results = []
    for spec in specs:
        try:
            results.append(fetch(spec, endpoint=args.endpoint, dry_run=args.dry_run, allow_unpinned=args.allow_unpinned))
        except Exception as exc:  # noqa: BLE001 - report every model, keep going
            results.append({"name": spec.name, "status": "failed", "error": str(exc)})
    for spec in registry.MODELS:
        if spec.gated and spec not in specs:
            results.append({"name": spec.name, "status": "skipped_gated", "note": spec.note})
    summary = {"models_dir": str(models_dir()), "results": results,
               "verified": {s.name: verify(s)["ok"] for s in specs if not s.gated}}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if any(r["status"] in ("failed", "refused_unpinned") for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
