#!/usr/bin/env python3
"""Prepare explicitly planned image inputs; never generate or inspect images.

Python 3.8+ standard library only. FFmpeg 5+ and ffprobe are needed only for new
frame extraction; --frame-manifest reuse does not invoke either executable.
Use a new or empty --out directory: existing outputs are never overwritten.
Exit codes: 0 = ready or waiting for dependencies, 1 = per-image/anchor errors,
2 = invalid invocation, plan envelope, or output directory.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
MODES = {"text_to_image", "image_to_image"}
KINDS = {"scene_description", "shot_frame", "neutral_background"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".avif", ".heic", ".heif", ".gif"}


class InputError(ValueError):
    pass


def fail(message):
    raise InputError(message)


def read_json(path):
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                fail("Duplicate JSON object key: " + key)
            result[key] = value
        return result

    with Path(path).open("r", encoding="utf-8-sig") as stream:
        return json.load(stream, object_pairs_hook=unique_keys,
                         parse_constant=lambda value: fail("Non-finite JSON number: " + value))


def valid_id(value, label):
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        fail(label + " must match [A-Za-z0-9][A-Za-z0-9_-]{0,127}")
    return value


def required_string(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        fail(key + " must be a nonempty string")
    return value


def required_list(obj, key):
    value = obj.get(key)
    if not isinstance(value, list):
        fail(key + " must be an array")
    return value


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def positive_integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def anchor_location(anchor):
    frame_index = (anchor["frame_number"] - anchor["frame_number_base"]
                   if anchor["frame_number"] is not None else None)
    return anchor["shot_id"], anchor["time_s"], frame_index


def validate_anchor(anchor):
    if not isinstance(anchor, dict):
        fail("Each anchor must be an object")
    for key in ("id", "shot_id", "time_s", "time_precision", "role", "description",
                "frame_number", "frame_number_base"):
        if key not in anchor:
            fail("Anchor missing field: " + key)
    valid_id(anchor["id"], "anchor.id")
    if not positive_integer(anchor["shot_id"]):
        fail("anchor.shot_id must be a positive integer shot sequence number")
    required_string(anchor, "description")
    if anchor["role"] not in ("primary", "context"):
        fail("anchor.role must be primary or context")
    if anchor["time_precision"] not in ("exact", "approximate"):
        fail("anchor.time_precision must be exact or approximate")
    requested_time = anchor["time_s"]
    if requested_time is not None and (not number(requested_time) or requested_time < 0):
        fail("anchor.time_s must be a finite nonnegative number or null")
    frame = anchor["frame_number"]
    base = anchor["frame_number_base"]
    if frame is not None:
        if not isinstance(frame, int) or isinstance(frame, bool):
            fail("anchor.frame_number must be an integer or null")
        if isinstance(base, bool) or not isinstance(base, int) or base not in (0, 1):
            fail("An explicit frame_number_base of 0 or 1 is required with frame_number")
        if frame - base < 0:
            fail("frame_number - frame_number_base must be nonnegative")
    else:
        if base is not None:
            fail("frame_number_base must be null when frame_number is null")
        if requested_time is None:
            fail("An anchor requires frame_number or time_s")


def conflicting_anchor_ids(items):
    """Preflight locations so every use of an ambiguous anchor is rejected."""
    locations = {}
    conflicts = set()
    for item in items:
        source = item.get("space_source") if isinstance(item, dict) else None
        anchors = source.get("anchors") if isinstance(source, dict) else None
        if not isinstance(anchors, list):
            continue
        for anchor in anchors:
            try:
                validate_anchor(anchor)
            except (InputError, TypeError, ValueError):
                continue  # The normal per-image validator reports malformed anchors.
            location = anchor_location(anchor)
            if anchor["id"] in locations and locations[anchor["id"]] != location:
                conflicts.add(anchor["id"])
            locations[anchor["id"]] = location
    return conflicts


def validate_image(item, earlier_ids, duplicate_ids, anchor_conflicts):
    if not isinstance(item, dict):
        fail("Each image_prompts entry must be an object")
    image_id = valid_id(item.get("id"), "image.id")
    if image_id in duplicate_ids:
        fail("Duplicate image id: " + image_id)
    required_string(item, "ref")
    required_string(item, "prompt")
    mode = item.get("generation_mode")
    if mode not in MODES:
        fail("generation_mode must be text_to_image or image_to_image")
    source = item.get("space_source")
    if not isinstance(source, dict) or source.get("kind") not in KINDS:
        fail("space_source.kind must be scene_description, shot_frame, or neutral_background")
    anchors = required_list(source, "anchors")
    references = required_list(item, "reference_image_ids")
    appearances = required_list(item, "appearance_sources")
    if "shot_ids" in item:
        shot_ids = required_list(item, "shot_ids")
        if any(not positive_integer(shot_id) for shot_id in shot_ids):
            fail("shot_ids must contain positive integer shot sequence numbers")
        if len(set(shot_ids)) != len(shot_ids):
            fail("shot_ids must not contain duplicates")
    for anchor in anchors:
        validate_anchor(anchor)
        if anchor["id"] in anchor_conflicts:
            fail("Anchor id has conflicting locations across image tasks: " + anchor["id"])
        if anchor["role"] == "primary" and "shot_ids" in item and anchor["shot_id"] not in item["shot_ids"]:
            fail("The primary anchor shot_id must belong to this image's shot_ids")
    anchor_ids = [anchor["id"] for anchor in anchors]
    if len(set(anchor_ids)) != len(anchor_ids):
        fail("Duplicate anchor id within image: " + image_id)
    if source["kind"] == "shot_frame":
        if sum(anchor["role"] == "primary" for anchor in anchors) != 1:
            fail("shot_frame requires exactly one primary anchor")
        if anchors[0]["role"] != "primary":
            fail("The primary anchor must come first; input order is never rearranged")
    elif anchors:
        fail("Only space_source.kind=shot_frame may contain frame anchors")
    for reference in references:
        valid_id(reference, "reference_image_ids entry")
    if len(set(references)) != len(references):
        fail("Duplicate reference_image_ids entry")
    dependency_ids = []
    for appearance in appearances:
        if not isinstance(appearance, dict):
            fail("appearance_sources entries must be objects")
        dependency_ids.append(valid_id(appearance.get("image_id"), "appearance_sources.image_id"))
    if references != dependency_ids:
        fail("reference_image_ids must exactly match appearance_sources[].image_id in order")
    for dependency_id in dependency_ids:
        if dependency_id not in earlier_ids or dependency_id in duplicate_ids:
            fail("Appearance dependency must name a unique preceding image: " + dependency_id)
    if mode == "text_to_image" and (anchors or appearances or references):
        fail("text_to_image requires empty anchors, appearance_sources, and reference_image_ids")
    if mode == "image_to_image" and not (anchors or appearances):
        fail("image_to_image requires at least one anchor or appearance dependency")
    return anchors, dependency_ids


def executable(requested, name):
    candidate = shutil.which(os.path.expanduser(requested or name))
    if not candidate:
        fail(name + " is unavailable; put it on PATH or pass --" + name)
    return candidate


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, check=False)
    if result.returncode:
        fail(Path(command[0]).name + " failed: " + result.stderr.strip()[-3000:])
    return result.stdout


def finite_float(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    return value if math.isfinite(value) else None


class FrameExtractor:
    def __init__(self, video, ffmpeg, ffprobe):
        self.video_arg = video
        self.ffmpeg_arg = ffmpeg
        self.ffprobe_arg = ffprobe
        self.initialized = False
        self.failure = None

    def initialize(self):
        if self.failure:
            fail(self.failure)
        if self.initialized:
            return
        try:
            if not self.video_arg:
                fail("--video is required for frame anchors")
            self.video = Path(self.video_arg).expanduser().resolve()
            if not self.video.is_file():
                fail("Video does not exist: " + str(self.video))
            self.ffmpeg = executable(self.ffmpeg_arg, "ffmpeg")
            self.ffprobe = executable(self.ffprobe_arg, "ffprobe")
            data = json.loads(run([
                self.ffprobe, "-v", "error", "-select_streams", "v:0", "-show_frames",
                "-show_entries", "frame=best_effort_timestamp_time,pts_time,pkt_pts_time,duration_time,pkt_duration_time:stream=start_time,duration",
                "-of", "json", str(self.video),
            ]))
            frames = data.get("frames", [])
            if not frames:
                fail("No decoded video frames found")
            timestamps = []
            for index, frame in enumerate(frames):
                timestamp = None
                for key in ("best_effort_timestamp_time", "pts_time", "pkt_pts_time"):
                    try:
                        value = float(frame[key])
                    except (KeyError, ValueError, TypeError):
                        continue
                    if math.isfinite(value):
                        timestamp = value
                        break
                if timestamp is None:
                    fail("No usable display timestamp for decoded frame " + str(index))
                timestamps.append(timestamp)
            self.origin = timestamps[0]
            self.times = [timestamp - self.origin for timestamp in timestamps]
            last_index = max(range(len(self.times)), key=self.times.__getitem__)
            last_time = self.times[last_index]
            # A real final-frame duration establishes the last display interval.
            # If absent, use the selected video stream's end; never borrow audio
            # duration, guess a VFR frame interval, or silently clamp past EOF.
            last_duration = None
            for key in ("duration_time", "pkt_duration_time"):
                duration = finite_float(frames[last_index].get(key))
                if duration is not None and duration > 0:
                    last_duration = duration
                    break
            self.available_end = last_time
            self.end_exclusive = False
            if last_duration is not None:
                self.available_end = last_time + last_duration
                self.end_exclusive = True
            else:
                for stream in data.get("streams", []):
                    start = finite_float(stream.get("start_time"))
                    duration = finite_float(stream.get("duration"))
                    if start is not None and duration is not None and duration > 0:
                        end = start + duration - self.origin
                        if end > last_time:
                            self.available_end = end
                            self.end_exclusive = True
                            break
            self.initialized = True
        except (InputError, OSError, ValueError) as exc:
            self.failure = str(exc)
            fail(self.failure)

    def extract(self, anchor, target):
        self.initialize()
        requested = anchor["time_s"]
        time_index = None
        if requested is not None:
            at_end = math.isclose(requested, self.available_end, rel_tol=0, abs_tol=1e-9)
            outside = ((requested > self.available_end or at_end) if self.end_exclusive
                       else (requested > self.available_end and not at_end))
            if outside:
                fail("Requested time_s %.9g exceeds the available video interval ending at %.9g seconds%s"
                     % (requested, self.available_end, " (exclusive)" if self.end_exclusive else ""))
            # Equal-distance timestamps choose the first displayed frame.
            time_index = min(range(len(self.times)), key=lambda n: (abs(self.times[n] - requested), n))
        if anchor["frame_number"] is not None:
            index = anchor["frame_number"] - anchor["frame_number_base"]
            selection = "explicit_frame_number"
            if index >= len(self.times):
                fail("Requested frame index %d is outside the %d decoded frames" % (index, len(self.times)))
            if time_index is not None and time_index != index:
                fail("frame_number and time_s identify different displayed frames (%d versus %d)" % (index, time_index))
        else:
            index = time_index
            selection = "nearest_normalized_display_timestamp"
        descriptor, temporary = tempfile.mkstemp(prefix=".extract-", suffix=".png", dir=str(target.parent))
        os.close(descriptor)
        try:
            run([
                self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
                "-i", str(self.video), "-map", "0:v:0", "-an", "-sn", "-dn",
                "-vf", "select=eq(n\\,%d)" % index, "-fps_mode", "passthrough", "-frames:v", "1",
                "-f", "image2", temporary,
            ])
            if not Path(temporary).stat().st_size:
                fail("ffmpeg produced no frame for index " + str(index))
            # link() creates the final name atomically and refuses existing files.
            os.link(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return dict(anchor, input_file=str(target), selection_method=selection,
                    input_sha256=file_sha256(target),
                    actual_frame_index=index, actual_time_s=self.times[index],
                    source_first_frame_timestamp_s=self.origin,
                    source_available_time_end_s=self.available_end,
                    source_available_time_end_exclusive=self.end_exclusive)


class FrameReuse:
    """Reuse only verified prepared frames; never run ffmpeg or fall back."""
    def __init__(self, manifest, video):
        self.manifest_path = Path(manifest).expanduser().resolve()
        self.video_arg = video
        self.records = None
        self.failure = None

    def initialize(self):
        if self.failure:
            fail(self.failure)
        if self.records is not None:
            return
        try:
            data = read_json(self.manifest_path)
            if not isinstance(data, dict) or type(data.get("schema_version")) is not int or data["schema_version"] != 1:
                fail("Unsupported frame manifest schema; expected schema_version=1")
            if data.get("status") not in ("ready", "error"):
                fail("Frame manifest status must be ready or error")
            if not self.video_arg:
                fail("--video is required to verify a frame manifest's source")
            video = Path(self.video_arg).expanduser().resolve()
            recorded_video = data.get("source_video")
            if not isinstance(recorded_video, str) or not Path(recorded_video).is_absolute():
                fail("Frame manifest source_video must be an absolute path")
            if Path(recorded_video).resolve() != video:
                fail("Frame manifest source video does not match --video")
            if not video.is_file() or file_sha256(video) != data.get("source_video_sha256"):
                fail("Frame manifest source video is missing or its SHA256 has changed")
            records = {}
            for record in required_list(data, "anchors"):
                validate_anchor(record)
                if record["id"] in records:
                    fail("Duplicate anchor id in frame manifest: " + record["id"])
                records[record["id"]] = record
            self.records = records
        except (InputError, OSError, ValueError, TypeError) as exc:
            self.failure = str(exc)
            fail(self.failure)

    def extract(self, anchor, target):
        self.initialize()
        cached = self.records.get(anchor["id"])
        if cached is None:
            fail("Anchor is missing from frame manifest: " + anchor["id"])
        if cached.get("status") != "ready":
            fail("Prepared anchor is not ready: " + anchor["id"])
        if anchor_location(anchor) != anchor_location(cached):
            fail("Anchor location does not match frame manifest: " + anchor["id"])
        filename = cached.get("input_file")
        if not isinstance(filename, str) or not Path(filename).is_absolute():
            fail("Prepared frame input_file must be an absolute path")
        path = Path(filename).resolve()
        if path.suffix.lower() != ".png" or not path.is_file():
            fail("Prepared frame PNG is missing: " + str(path))
        if file_sha256(path) != cached.get("input_sha256"):
            fail("Prepared frame SHA256 does not match: " + str(path))
        index = cached.get("actual_frame_index")
        if type(index) is not int or index < 0:
            fail("Invalid actual_frame_index in frame manifest")
        if anchor["frame_number"] is not None and index != anchor_location(anchor)[2]:
            fail("Prepared actual_frame_index disagrees with the explicit frame number")
        for key in ("actual_time_s", "source_first_frame_timestamp_s", "source_available_time_end_s"):
            if not number(cached.get(key)):
                fail("Invalid frame manifest numeric field: " + key)
        if type(cached.get("source_available_time_end_exclusive")) is not bool:
            fail("Invalid source_available_time_end_exclusive in frame manifest")
        if cached.get("selection_method") not in ("explicit_frame_number", "nearest_normalized_display_timestamp"):
            fail("Invalid selection_method in frame manifest")
        result = dict(anchor)
        for key in ("input_sha256", "actual_frame_index", "actual_time_s", "selection_method",
                    "source_first_frame_timestamp_s", "source_available_time_end_s", "source_available_time_end_exclusive"):
            result[key] = cached[key]
        result.update(input_file=str(path), reused_from_frame_manifest=str(self.manifest_path))
        return result


def create_output_directory(path):
    out = Path(path).expanduser().resolve()
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        fail("--out must be a new or empty directory; existing outputs are never overwritten: " + str(out))
    out.mkdir(parents=True, exist_ok=True)
    lock = out / ".prepare_image_inputs.lock"
    with lock.open("x", encoding="utf-8") as stream:
        stream.write(str(os.getpid()) + "\n")
    return out, lock


def prepare_anchors(args):
    source = read_json(Path(args.anchors).expanduser().resolve())
    if not isinstance(source, dict):
        fail("--anchors must contain an object with an anchors array")
    anchors = required_list(source, "anchors")
    seen = set()
    for anchor in anchors:
        validate_anchor(anchor)
        if anchor["id"] in seen:
            fail("--anchors requires unique anchor IDs: " + anchor["id"])
        seen.add(anchor["id"])
    video = Path(args.video).expanduser().resolve()
    if not video.is_file():
        fail("Video does not exist: " + str(video))
    source_hash = file_sha256(video)
    out, lock = create_output_directory(args.out)
    try:
        extractor = FrameExtractor(str(video), args.ffmpeg, args.ffprobe)
        records = []
        issues = []
        for position, anchor in enumerate(anchors, 1):
            record = dict(anchor, status="error", issues=[])
            try:
                target = out / ("%02d_%s.png" % (position, anchor["id"]))
                record.update(extractor.extract(anchor, target), status="ready")
            except (InputError, OSError, ValueError, TypeError) as exc:
                record["issues"].append(str(exc))
                issues.append(anchor["id"] + ": " + str(exc))
            records.append(record)
        if file_sha256(video) != source_hash:
            fail("Source video changed during frame extraction; no reusable manifest was written")
        status = "error" if issues else "ready"
        manifest_path = out / "frame_manifest.json"
        with manifest_path.open("x", encoding="utf-8") as stream:
            json.dump({"schema_version": 1, "status": status, "source_video": str(video),
                       "source_video_sha256": source_hash, "anchors": records, "issues": issues},
                      stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"manifest": str(manifest_path), "status": status}, ensure_ascii=False))
        return 1 if issues else 0
    finally:
        lock.unlink(missing_ok=True)


def process_plan(args):
    plan_path = Path(args.plan).expanduser().resolve()
    plan = read_json(plan_path)
    if not isinstance(plan, dict):
        fail("Plan must be a JSON object")
    for key in ("status", "image_prompts", "video_prompt", "issues"):
        if key not in plan:
            fail("Plan missing top-level field: " + key)
    if plan.get("status") not in ("complete", "partial", "needs_input"):
        fail("Plan status must be complete, partial, or needs_input")
    items = required_list(plan, "image_prompts")
    if plan["status"] == "needs_input" and items:
        fail("A needs_input plan must have an empty image_prompts array")
    required_list(plan, "issues")
    generated = {}
    if args.generated_manifest:
        manifest_path = Path(args.generated_manifest).expanduser().resolve()
        generated = read_json(manifest_path)
        if not isinstance(generated, dict):
            fail("--generated-manifest must contain an object mapping image_id to file path")
        for image_id, path in list(generated.items()):
            valid_id(image_id, "generated manifest image_id")
            if not isinstance(path, str) or not path.strip():
                fail("Generated image paths must be nonempty strings")
            value = Path(path).expanduser()
            if not value.is_absolute():
                value = manifest_path.parent / value
            generated[image_id] = value.resolve()
    out, lock = create_output_directory(args.out)
    try:
        counts = {}
        for item in items:
            image_id = item.get("id") if isinstance(item, dict) else None
            if isinstance(image_id, str):
                counts[image_id] = counts.get(image_id, 0) + 1
        duplicates = {key for key, count in counts.items() if count > 1}
        anchor_conflicts = conflicting_anchor_ids(items)
        extractor = (FrameReuse(args.frame_manifest, args.video) if args.frame_manifest
                     else FrameExtractor(args.video, args.ffmpeg, args.ffprobe))
        records = []
        earlier_ids = set()
        for item in items:
            item_dict = item if isinstance(item, dict) else {}
            record = {key: item_dict.get(key) for key in ("id", "ref", "prompt", "generation_mode")}
            record.update(status="error", input_files=[], anchors=[], appearance_inputs=[], issues=[])
            try:
                anchors, dependencies = validate_image(item, earlier_ids, duplicates, anchor_conflicts)
                missing = []
                dependency_files = []
                for dependency_id in dependencies:
                    path = generated.get(dependency_id)
                    if path is None or not path.is_file():
                        missing.append(dependency_id)
                        record["appearance_inputs"].append({"image_id": dependency_id, "input_file": None})
                    else:
                        if path.suffix.lower() not in IMAGE_SUFFIXES:
                            fail("Appearance dependency must be an image file, not video or other input: " + str(path))
                        dependency_files.append(str(path))
                        record["appearance_inputs"].append({"image_id": dependency_id, "input_file": str(path)})
                if anchors:
                    image_out = out / item["id"]
                    if not args.frame_manifest:
                        image_out.mkdir(exist_ok=False)
                    for position, anchor in enumerate(anchors, 1):
                        target = image_out / ("%02d_%s.png" % (position, anchor["id"]))
                        actual = extractor.extract(anchor, target)
                        record["anchors"].append(actual)
                        record["input_files"].append(actual["input_file"])
                record["input_files"].extend(dependency_files)
                record["missing_dependency_ids"] = missing
                record["status"] = "waiting_dependency" if missing else "ready"
                if missing:
                    record["issues"].append("Generated appearance images unavailable: " + ", ".join(missing))
            except (InputError, OSError, ValueError, TypeError) as exc:
                record["issues"].append(str(exc))
            records.append(record)
            if isinstance(item_dict.get("id"), str):
                earlier_ids.add(item_dict["id"])
        errors = sum(record["status"] == "error" for record in records)
        waiting = sum(record["status"] == "waiting_dependency" for record in records)
        status = "error" if errors else "waiting_dependency" if waiting else "ready"
        result = {
            "status": status,
            "source_plan": str(plan_path),
            "source_plan_status": plan["status"],
            "source_video": str(Path(args.video).expanduser().resolve()) if args.video else None,
            "image_prompts": records,
            "video_prompt": plan["video_prompt"],
            "issues": plan["issues"],
            "summary": {"ready": len(records) - errors - waiting, "waiting_dependency": waiting, "error": errors},
            "input_order": "frame anchors in declared order, followed by appearance dependencies in declared order",
        }
        result_path = out / "input_manifest.json"
        with result_path.open("x", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        print(json.dumps({"manifest": str(result_path), "status": status, "summary": result["summary"]}, ensure_ascii=False))
        return 1 if errors else 0
    finally:
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--plan", help="Final SP planning JSON, including prompts")
    inputs.add_argument("--anchors", help="Pre-prompt JSON object containing only the requested anchors array")
    parser.add_argument("--video", help="Whitebox video; required with --anchors and for frame anchors in --plan")
    parser.add_argument("--out", required=True, help="New or empty output directory")
    parser.add_argument("--ffmpeg", help="ffmpeg executable, otherwise discovered on PATH")
    parser.add_argument("--ffprobe", help="ffprobe executable, otherwise discovered on PATH")
    parser.add_argument("--generated-manifest", help="JSON object mapping preceding image IDs to generated image paths; relative paths resolve against this JSON file")
    parser.add_argument("--frame-manifest", help="With --plan, strictly reuse frames from a prepared frame_manifest.json without invoking ffmpeg or falling back")
    args = parser.parse_args()
    if args.anchors and not args.video:
        parser.error("--video is required with --anchors")
    if args.anchors and (args.frame_manifest or args.generated_manifest):
        parser.error("--frame-manifest and --generated-manifest are supported only with --plan")
    try:
        return prepare_anchors(args) if args.anchors else process_plan(args)
    except (InputError, OSError, ValueError, TypeError) as exc:
        print("error: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
