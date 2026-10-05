"""wbs — command line for the white-model production platform."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import typer

from . import __version__
from .config import REPO_ROOT, load_settings, load_yaml_config
from .errors import WbsError
from .layout import Workspace, check_id, job_from_path
from .ledger import Ledger
from .log import setup_logging

app = typer.Typer(add_completion=False, no_args_is_help=True, help="白模视频生产平台（whitebox-studio）")
forward_app = typer.Typer(no_args_is_help=True, help="正向构建：结构树批量 / 故事驱动 / 长镜头规划")
reverse_app = typer.Typer(no_args_is_help=True, help="反推：分析真实视频 → 求解 scene.json → 闸门；在线转换提交包")
v2v_app = typer.Typer(no_args_is_help=True, help="白模转真人（Skill v3）：规划 → 生图 → 打包 → 提交")
review_app = typer.Typer(no_args_is_help=True, help="人工复核标记")
batch_app = typer.Typer(no_args_is_help=True, help="批次收尾")
taxonomy_app = typer.Typer(no_args_is_help=True, help="视频结构树")
cost_app = typer.Typer(no_args_is_help=True, help="成本台账")
registry_app = typer.Typer(no_args_is_help=True, help="入库目录与看板")
vision_app = typer.Typer(no_args_is_help=True, help="可选视觉模型：状态、准备、下载（默认关闭，缺失时自动回退）")
for sub, name in ((forward_app, "forward"), (reverse_app, "reverse"), (v2v_app, "v2v"), (review_app, "review"),
                  (batch_app, "batch"), (taxonomy_app, "taxonomy"), (cost_app, "cost"), (registry_app, "registry"),
                  (vision_app, "vision")):
    app.add_typer(sub, name=name)

STATE: dict[str, Any] = {"verbose": False}


@app.callback()
def main(verbose: bool = typer.Option(False, "--verbose", "-v", help="输出调试日志")) -> None:
    STATE["verbose"] = verbose


def _ws() -> Workspace:
    settings = load_settings()
    ws = Workspace(settings.workspace).ensure()
    setup_logging(ws.root, STATE["verbose"])
    return ws


def _providers(ws: Workspace, confirm_paid: bool, dry_run: bool):
    from .providers import get_providers

    return get_providers(load_settings(), Ledger(ws.ledger_path), confirmed=confirm_paid, dry_run=dry_run)


def _out(data: Any) -> None:
    typer.echo(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _run(fn):
    try:
        return fn()
    except (WbsError, FileNotFoundError, ValueError, KeyError) as exc:
        if STATE["verbose"]:
            raise
        typer.secho(f"错误：{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


# --------------------------------------------------------------------------- setup
@app.command()
def version() -> None:
    """显示版本。"""
    typer.echo(__version__)


@app.command()
def init() -> None:
    """创建工作区目录（批次、台账、看板、日志）。"""
    ws = _ws()
    _out({"workspace": str(ws.root), "ledger": str(ws.ledger_path), "configs": str(REPO_ROOT / "configs")})


@app.command()
def doctor() -> None:
    """检查运行环境：工具、模型文件、依赖、模型提供方配置（只显示环境变量是否存在，不显示值）。"""
    from .tools import find_tool, tool_version

    ws = _ws()
    report: dict[str, Any] = {"python": sys.version.split()[0], "workspace": str(ws.root), "tools": {}}
    for name in ("blender", "ffmpeg", "ffprobe", "face_model"):
        path = find_tool(name)
        report["tools"][name] = {"path": path, "version": tool_version(name) if path and name != "face_model" else None}
    providers = load_yaml_config(load_settings().providers_file)
    report["providers"] = {}
    for kind, cfg in providers.items():
        envs = {k: v for k, v in cfg.items() if k.endswith("_env") and v}
        report["providers"][kind] = {"provider": cfg.get("provider"), "model": cfg.get("model"),
                                     "env_present": {v: bool(os.environ.get(v)) for v in envs.values()}}
    report["paid_calls_require_confirmation"] = load_settings().require_paid_confirmation
    from .vision import capabilities

    caps = capabilities()
    report["vision"] = {"configured": caps["configured"], "worker_installed": caps["worker"].get("installed", False),
                        "gpu": caps["worker"].get("device"),
                        "ready": [f"{k}:{n}" for k, names in caps["backends"].items() for n, s in names.items() if s == "ok"],
                        "note": "可选组件，默认关闭；详见 wbs vision status"}
    report["ok"] = all(report["tools"][n]["path"] for n in ("blender", "ffmpeg", "ffprobe"))
    _out(report)
    if not report["ok"]:
        raise typer.Exit(1)


@vision_app.command("status")
def vision_status(probe: bool = typer.Option(False, "--probe", help="重新启动视觉工作进程自检（否则用缓存）")) -> None:
    """可选视觉后端：配置、依赖、模型文件（sha256）、工作环境与 GPU、各后端能否运行及原因。"""
    from .vision import capabilities

    _out(capabilities(probe_worker=probe))


@vision_app.command("prepare")
def vision_prepare(force: bool = typer.Option(False, "--force")) -> None:
    """一次性准备：视觉工作环境自检；由 TransNetV2 权重导出 ONNX（主环境用 onnxruntime 推理）。"""
    from .vision.transnet import onnx_path
    from .vision.worker import run, selftest

    def go():
        result: dict[str, Any] = {"selftest": {k: v for k, v in selftest(refresh=True).items() if k != "stamp"}}
        if force or not onnx_path().exists():
            result["transnetv2_onnx"] = run("export_transnetv2", {})
        else:
            result["transnetv2_onnx"] = {"onnx": str(onnx_path()), "status": "exists"}
        _out(result)
    _run(go)


@vision_app.command("fetch")
def vision_fetch(profile: str = typer.Option("default", help="default | full | mapanything"),
                 models: str = typer.Option("", help="逗号分隔的模型名，覆盖 profile"),
                 endpoint: str = typer.Option("huggingface", help="huggingface | hf-mirror | modelscope | 兼容地址"),
                 dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """下载可选模型权重：只用公开地址，固定版本 + sha256 校验；门控仓库（SAM 3 等）只报告、不申请。"""
    from .vision import registry
    from .vision.fetch import fetch

    def go():
        specs = [registry.get(n.strip()) for n in models.split(",") if n.strip()] or registry.profile(profile)
        gated = [m for m in registry.MODELS if m.gated and m not in specs]
        _out([fetch(s, endpoint=endpoint, dry_run=dry_run) for s in specs] +
             [{"name": m.name, "status": "skipped_gated", "note": m.note} for m in gated])
    _run(go)


@app.command("schemas")
def schemas_cmd() -> None:
    """重新导出 schemas/*.schema.json。"""
    from .models import export_schemas

    _out([str(p.relative_to(REPO_ROOT)) for p in export_schemas(REPO_ROOT / "schemas")])


# --------------------------------------------------------------------------- taxonomy
@taxonomy_app.command("sample")
def taxonomy_sample(n: int = typer.Option(10, help="条数"), seed: int = typer.Option(1, help="随机种子"),
                    narrative_ratio: float = typer.Option(0.5, help="叙事类占比"),
                    subjects: str = typer.Option("", help="限定主体，逗号分隔：none,person,animal,object")) -> None:
    """按结构树抽样控制信息（可复现）。"""
    from .taxonomy import sample_controls

    specs = sample_controls(n, seed, narrative_ratio, [s for s in subjects.split(",") if s] or None)
    _out([{**s.as_dict(), **s.labels()} for s in specs])


# --------------------------------------------------------------------------- forward
@forward_app.command("batch")
def forward_batch(batch: str = typer.Option(..., help="批次 ID"), n: int = typer.Option(10, help="条数"),
                  seed: int = typer.Option(1, help="随机种子"), narrative_ratio: float = typer.Option(0.5),
                  subjects: str = typer.Option("", help="限定主体，逗号分隔"),
                  render: bool = typer.Option(True, "--render/--no-render", help="生成后直接渲染并质检"),
                  workers: int = typer.Option(1, help="并行渲染数（每个占用一个 Blender 进程）"),
                  engine: str = typer.Option(None, help="workbench | eevee"),
                  rewrite: bool = typer.Option(True, "--rewrite/--no-rewrite", help="LLM 把控制层 prompt 改写成建模提示词（默认 mock）"),
                  confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """结构树批量：抽样 → scene.json（渲染前动态闸门＋构图预检，不过则换种子）→ 建模提示词改写 → 渲染 → 质检。"""
    from .forward.batch import plan_batch, rewrite_prompts
    from .orchestrate import process_jobs

    def go():
        ws = _ws()
        settings = load_settings()
        jobs = plan_batch(ws, settings, check_id(batch, "batch id"), n, seed, narrative_ratio,
                          [s for s in subjects.split(",") if s] or None)
        result: dict[str, Any] = {"batch": batch, "planned": len(jobs),
                                  "pregate_failed": [j.key for j in jobs if j.read_meta().get("pregate") != "passed"]}
        if rewrite:
            result["rewrite"] = rewrite_prompts(jobs, _providers(ws, confirm_paid, dry_run))
        if render:
            result["process"] = process_jobs(ws, settings, jobs, workers=workers, engine=engine)
        _out(result)
    _run(go)


def _new_job(ws: Workspace, batch: str, job: str):
    paths = ws.job(check_id(batch, "batch id"), check_id(job, "job id"))
    paths.root.mkdir(parents=True, exist_ok=True)
    return paths


def _save_story(ws: Workspace, paths, plan, info: dict[str, Any], brief: str, spec, render: bool) -> dict[str, Any]:
    from .forward.story import story_to_scene
    from .jsonio import write_json
    from .orchestrate import process_jobs
    from .qc.pregate import pregate, write_pregate

    settings = load_settings()
    write_json(paths.story_plan, plan.model_dump(mode="json"))
    scene = story_to_scene(plan, spec, paths.job_id, brief, gate=settings.qc.dynamic_gate,
                           dressing=settings.forward.dressing).to_json_dict()
    write_json(paths.scene, scene)
    checked = pregate(scene, settings)
    write_pregate(paths, checked)
    write_json(paths.report("故事评审.json"), {"schema": "wbs.story_review/1.0", **info})
    paths.update_meta(job_id=paths.job_id, batch_id=paths.batch_id, route="forward_story", title=scene["title"],
                      brief=brief, plan={k: info.get(k) for k in ("mode", "start", "notes", "simulated", "examples")},
                      pregate=checked["status"], grammar=checked["grammar"]["status"], shots=len(scene["shots"]),
                      status="planned")
    result: dict[str, Any] = {"job": paths.key, "title": scene["title"], "simulated_plan": info.get("simulated"),
                              "pregate": checked["status"], "grammar": checked["grammar"]["status"],
                              "warnings": [c for c in info.get("mechanical_checks", []) if c["status"] != "ok"]}
    if render:
        result["process"] = process_jobs(ws, settings, [paths], force=True)
    return result


@forward_app.command("story")
def forward_story(batch: str = typer.Option(...), job: str = typer.Option(...), brief: str = typer.Option(..., help="需求描述"),
                  content_class: str = typer.Option("narrative", help="narrative | motion"),
                  mode: str = typer.Option("chain", help="chain：分层规划链（默认）｜single：单次规划"),
                  plan_file: Path = typer.Option(None, "--plan", exists=True, dir_okay=False,
                                                 help="导入外部撰写的 StoryPlan JSON（跳过规划模型，契约校验照常）"),
                  render: bool = typer.Option(True, "--render/--no-render"),
                  confirm_paid: bool = typer.Option(False, "--confirm-paid", help="确认允许真实（可能付费）的模型调用"),
                  dry_run: bool = typer.Option(False, "--dry-run", help="只记录预估，不真实调用")) -> None:
    """故事驱动：需求 → 分层规划（前提→人物→事件链→空间→分镜，默认 mock）→ scene.json → 渲染与质检。"""
    from .forward.story import plan_story
    from .forward.story_chain import mechanical_checks, run_chain
    from .jsonio import read_json
    from .models.story import StoryPlan
    from .providers import CallContext

    def go():
        ws = _ws()
        settings = load_settings()
        paths = _new_job(ws, batch, job)
        spec = settings.spec("forward_story")
        providers = _providers(ws, confirm_paid, dry_run)
        ctx = CallContext(paths.key, "story_plan", paths.batch_id)
        if plan_file is not None:
            plan = StoryPlan.model_validate(read_json(plan_file))
            info = {"mode": "external", "source": plan_file.name, "simulated": False,
                    "notes": "外部撰写的 StoryPlan，未调用规划模型", "mechanical_checks": mechanical_checks(plan)}
        elif mode == "single":
            plan, info = plan_story(brief, providers, ctx, spec, content_class)
            info = {**info, "mode": "single", "mechanical_checks": mechanical_checks(plan)}
        elif mode == "chain":
            plan, info = run_chain(brief, providers, ctx, spec, content_class)
        else:
            raise ValueError("mode 必须是 chain 或 single")
        _out(_save_story(ws, paths, plan, info, brief, spec, render))
    _run(go)


@forward_app.command("import-layout")
def forward_import_layout(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
                          layout: Path = typer.Option(..., exists=True, dir_okay=False,
                                                      help="Infinigen / 任意 .blend，或 wbs.layout JSON"),
                          keep_ground: bool = typer.Option(True, "--keep-ground/--replace-ground"),
                          offset: str = typer.Option("0,0", help="布局整体平移 x,y（米）"),
                          exclude: str = typer.Option("", help="跳过的物体名前缀，逗号分隔")) -> None:
    """[可选] 把室内布局导入为体块（替换场景体块，保留相机与主体路线），重跑动态闸门并报告碰撞。"""
    from .forward.layout import import_layout

    def go():
        _ws()
        dx, dy = (float(v) for v in offset.split(","))
        _out(import_layout(job_from_path(job_dir), load_settings(), layout, keep_ground=keep_ground, offset=(dx, dy),
                           exclude=exclude))
    _run(go)


@forward_app.command("revise")
def forward_revise(job_dir: Path = typer.Argument(..., exists=True, file_okay=False, help="故事驱动任务目录"),
                   notes: str = typer.Option(..., help="看片意见（会写进规划提示词）"),
                   level: str = typer.Option(..., help="story：故事或节奏需调整（从事件链重做）｜shots：调度或画面需调整（只重做分镜）"),
                   render: bool = typer.Option(True, "--render/--no-render"),
                   confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """[D5] 看片回路：按意见修订规划 → 重建 scene.json → 重渲与质检。修订前的交付物先备份到 versions/vNN。"""
    from .forward.story_chain import revise_plan, snapshot_version
    from .jsonio import read_json
    from .models.story import StoryPlan
    from .providers import CallContext

    def go():
        ws = _ws()
        settings = load_settings()
        paths = job_from_path(job_dir)
        if not paths.story_plan.exists():
            raise ValueError(f"{paths.key} 没有 story_plan.json，只有故事驱动任务可以修订")
        old = StoryPlan.model_validate(read_json(paths.story_plan))
        meta = paths.read_meta()
        backup = snapshot_version(paths)
        plan, info = revise_plan(old, meta.get("brief", old.logline), notes, level, _providers(ws, confirm_paid, dry_run),
                                 CallContext(paths.key, f"story_revise_{level}", paths.batch_id), settings.spec("forward_story"))
        result = _save_story(ws, paths, plan, {**info, "revision_of": backup.name}, meta.get("brief", ""),
                             settings.spec("forward_story"), render)
        history = list(meta.get("revisions", [])) + [{"at": backup.name, "level": level, "notes": notes}]
        paths.update_meta(revisions=history)
        _out({**result, "backup": str(backup), "level": level})
    _run(go)


@forward_app.command("longtake")
def forward_longtake(batch: str = typer.Option(...), job: str = typer.Option(...), brief: str = typer.Option(...),
                     zones: int = typer.Option(5, help="区域数"), subject: bool = typer.Option(True, "--subject/--no-subject"),
                     render: bool = typer.Option(True, "--render/--no-render"),
                     confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """长镜头规划：需求 → 区域序列（规划模型，默认 mock）→ 相通房间 + 一镜到底相机 → 渲染与质检。"""
    from .forward.longtake import plan_longtake, plan_to_scene
    from .jsonio import write_json
    from .orchestrate import process_jobs
    from .providers import CallContext
    from .qc.pregate import pregate, write_pregate

    def go():
        ws = _ws()
        settings = load_settings()
        paths = _new_job(ws, batch, job)
        spec = settings.spec("forward_longtake")
        plan, info = plan_longtake(brief, _providers(ws, confirm_paid, dry_run),
                                   CallContext(paths.key, "longtake_plan", paths.batch_id), spec, zones, subject)
        write_json(paths.longtake_plan, plan.model_dump())
        scene = plan_to_scene(plan, spec, paths.job_id, brief).to_json_dict()
        write_json(paths.scene, scene)
        checked = pregate(scene, settings)
        write_pregate(paths, checked)
        paths.update_meta(job_id=paths.job_id, batch_id=paths.batch_id, route="forward_longtake", title=scene["title"],
                          brief=brief, plan=info, pregate=checked["status"], grammar=checked["grammar"]["status"],
                          shots=1, status="planned")
        result: dict[str, Any] = {"job": paths.key, "title": scene["title"], "zones": [z.name for z in plan.zones],
                                  "simulated_plan": info["simulated"], "pregate": checked["status"]}
        if render:
            result["process"] = process_jobs(ws, settings, [paths])
        _out(result)
    _run(go)


# --------------------------------------------------------------------------- render / qc
@app.command()
def render(target: str = typer.Argument(..., help="任务目录、批次目录或批次 ID"),
           engine: str = typer.Option(None, help="workbench | eevee"), force: bool = typer.Option(False, help="忽略台账强制重渲"),
           workers: int = typer.Option(1), keep_frames: bool = typer.Option(False, help="保留 PNG 帧序列"),
           qc: bool = typer.Option(True, "--qc/--no-qc", help="渲染后质检"),
           passes: str = typer.Option("", help="额外控制通道（供 V2V 模型）：depth,seg,edge 任选，逗号分隔")) -> None:
    """渲染（Blender 无头）+ 编码 + 故事板 + 文本产物；台账记录，可断点续跑。可选输出深度/分割/边缘控制通道。"""
    from .control_passes import render_passes
    from .orchestrate import process_jobs, resolve_targets

    def go():
        ws = _ws()
        settings = load_settings()
        jobs = resolve_targets(ws, target)
        result = process_jobs(ws, settings, jobs, render=True, qc=qc, workers=workers, force=force, engine=engine,
                              keep_frames=keep_frames)
        names = [p.strip() for p in passes.split(",") if p.strip()]
        if names:
            result["passes"] = {j.key: render_passes(j, settings, names) for j in jobs}
        _out(result)
    _run(go)


@app.command("export")
def export_cmd(target: str = typer.Argument(..., help="任务目录、批次目录或批次 ID（需已渲染出 .blend）"),
               formats: str = typer.Option("fbx,glb", help="fbx、glb 任选，逗号分隔")) -> None:
    """导出模型：FBX（米制、逐帧烘焙动画）、GLB（整场一段动画）与 镜头切换.csv，写入任务的 模型导出/（已有则另建带时间的目录）。"""
    from .export import export_models
    from .orchestrate import resolve_targets

    def go():
        names = tuple(f.strip().lower() for f in formats.split(",") if f.strip())
        bad = sorted(set(names) - {"fbx", "glb"})
        if bad or not names:
            raise WbsError(f"--formats 只支持 fbx、glb：{', '.join(bad) or '（空）'}")
        settings = load_settings()
        _out({j.key: export_models(j, settings, names) for j in resolve_targets(_ws(), target)})
    _run(go)


@app.command("qc")
def qc_cmd(target: str = typer.Argument(..., help="任务目录、批次目录或批次 ID")) -> None:
    """重新运行全部自动检查并写报告（不改人工复核状态）。"""
    from .orchestrate import process_jobs, resolve_targets

    def go():
        ws = _ws()
        _out(process_jobs(ws, load_settings(), resolve_targets(ws, target), render=False, qc=True))
    _run(go)


# --------------------------------------------------------------------------- reverse
@reverse_app.command("analyze")
def reverse_analyze(batch: str = typer.Option(...), job: str = typer.Option(...),
                    video: Path = typer.Option(..., exists=True, dir_okay=False, help="源视频"),
                    license_note: str = typer.Option(..., "--license", help="授权来源（必填），例如“自有拍摄”"),
                    annotate: bool = typer.Option(True, "--annotate/--no-annotate", help="视觉模型标注主体框（默认 mock）"),
                    cuts: str = typer.Option(None, help="切镜：builtin | pyscenedetect | transnetv2（默认读配置 vision.cuts）"),
                    subject_model: str = typer.Option(None, help="主体：motion | grounding_dino | sam3（默认读配置 vision.subject）"),
                    subject: str = typer.Option("person", help="主体类别（决定检测提示词）"),
                    prompt: str = typer.Option(None, help="主体检测提示词，覆盖默认（如 \"red car.\"）"),
                    camera_motion: str = typer.Option(None, help="运镜标签：rules | llm（CameraBench 分类体系）"),
                    confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """登记授权 → 抽帧（13 张均匀＋首中尾密集）→ 切镜 → 背景光流 → 主体占幅 → 结构线 → 运镜标签。"""
    from .reverse.pipeline import intake, run_analysis

    def go():
        ws = _ws()
        paths = _new_job(ws, batch, job)
        intake(paths, video, license_note)
        analysis = run_analysis(paths, _providers(ws, confirm_paid, dry_run), annotate=annotate, cuts=cuts,
                                subject_backend=subject_model, subject=subject, prompt=prompt, camera_motion=camera_motion)
        paths.update_meta(job_id=paths.job_id, batch_id=paths.batch_id, title=f"反推_{paths.job_id}")
        _out({"job": paths.key, "cuts_s": analysis["cuts"]["times_s"], "shots": len(analysis["shots"]),
              "cuts_backend": analysis["cuts"]["detector"]["backend"],
              "subject_backend": analysis["subject_detection"]["backend"],
              "camera_motion": {s["id"]: s.get("camera_motion", {}).get("labels") for s in analysis["shots"]},
              "annotation": analysis.get("annotation"), "analysis": str(paths.analysis_dir / "analysis.json")})
    _run(go)


@reverse_app.command("solve")
def reverse_solve(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
                  subject: str = typer.Option("person", help="person|animal|bird|fish|vehicle|robot|product"),
                  lens: float = typer.Option(28.0, help="假定焦距 mm（启发式求解用；几何求解用实测焦距）"),
                  max_refine: int = typer.Option(2),
                  geometry: str = typer.Option(None, help="heuristic | da3 | mapanything | npz（默认读配置 vision.geometry）"),
                  track: Path = typer.Option(None, exists=True, dir_okay=False, help="外部相机轨迹 NPZ（MegaSaM / wbs 格式）"),
                  camera_height: float = typer.Option(1.6, help="假定相机高度 m（尺度来源 camera_height 时用）"),
                  scale: str = typer.Option("camera_height", help="几何尺度：camera_height（假定机高）| metric（DA3METRIC-LARGE）| auto"),
                  render: bool = typer.Option(True, "--render/--no-render", help="求解后渲染整段并质检"),
                  confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """求解 scene.json 草稿（几何或启发式）→ 静态闸门（首/中/尾帧对比）→ 修正循环 → 动态闸门 →（可选）整段渲染。"""
    from .orchestrate import process_jobs
    from .reverse.pipeline import solve_and_gate

    def go():
        ws = _ws()
        paths = job_from_path(job_dir)
        result = solve_and_gate(paths, load_settings(), _providers(ws, confirm_paid, dry_run), subject=subject,
                                lens_mm=lens, max_refine=max_refine, geometry=geometry, track_file=track,
                                camera_height=camera_height, scale=scale)
        if render:
            result["process"] = process_jobs(ws, load_settings(), [paths], force=True)
        _out(result)
    _run(go)


@reverse_app.command("online-package")
def reverse_online(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
                   subject: str = typer.Option("person", help="person|bird|vehicle（选择默认主体形态描述）")) -> None:
    """在线模型转换提交包：四宫格白模样板图 + 提示词 + 人脸打码原视频（只导出，不上传）。"""
    from .reverse.online import build_package

    def go():
        _ws()
        paths = job_from_path(job_dir)
        manifest = build_package(paths, load_settings(), paths.root / paths.read_meta()["source_video"], subject=subject)
        _out({k: manifest[k] for k in ("status", "uploaded", "manual_checks", "face_blur")})
    _run(go)


# --------------------------------------------------------------------------- v2v
def _v2v_job(ws: Workspace, job_dir: Path | None, inputs_dir: Path | None, batch: str | None, job: str | None):
    from .jsonio import write_json
    from .v2v import pipeline, planner

    if job_dir is not None:
        paths = job_from_path(job_dir)
        return paths, pipeline.job_inputs(paths)
    if inputs_dir is None or not batch or not job:
        raise ValueError("给出任务目录，或同时给出 --inputs 四文件目录、--batch 和 --job")
    paths = _new_job(ws, batch, job)
    inputs = planner.inputs_from_dir(inputs_dir, paths.job_id)
    if not paths.job_json.exists():
        paths.update_meta(job_id=paths.job_id, batch_id=paths.batch_id, route="v2v_only", title=inputs.video.stem,
                          inputs_dir=str(inputs_dir))
        write_json(paths.root / "v2v_inputs.json", {"folder": str(inputs_dir)})
    return paths, inputs


def _v2v_options(max_images: int | None, style: str | None, aspect: str | None, adaptation: str | None) -> dict[str, Any]:
    return {"max_images": max_images, "style": style, "aspect": aspect, "adaptation": adaptation}


@v2v_app.command("run")
def v2v_run(job_dir: Path = typer.Argument(None, exists=True, file_okay=False, help="白模任务目录"),
            inputs: Path = typer.Option(None, "--inputs", exists=True, file_okay=False, help="或：四文件所在目录"),
            batch: str = typer.Option(None), job: str = typer.Option(None),
            max_images: int = typer.Option(None), style: str = typer.Option(None), aspect: str = typer.Option(None),
            adaptation: str = typer.Option(None, help="用户明确的改编（会写进规划输入）"),
            images: bool = typer.Option(True, "--images/--prompts-only", help="只要提示词时不调用生图"),
            confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """三个节点一次跑完：规划（SP输出_v3.json）→ 生图 → 绑定打包。提交需另行执行 wbs v2v submit。"""
    from .v2v import pipeline

    def go():
        ws = _ws()
        paths, inp = _v2v_job(ws, job_dir, inputs, batch, job)
        providers = _providers(ws, confirm_paid, dry_run)
        options = _v2v_options(max_images, style, aspect, adaptation)
        result: dict[str, Any] = {"job": paths.key, "plan": pipeline.run_plan(paths, inp, providers, options)}
        if images:
            result["images"] = pipeline.run_images(paths, inp, providers)
            result["package"] = pipeline.run_package(paths, inp, providers, options)
        _out(result)
    _run(go)


@v2v_app.command("plan")
def v2v_plan(job_dir: Path = typer.Argument(None, exists=True, file_okay=False),
             inputs: Path = typer.Option(None, "--inputs", exists=True, file_okay=False),
             batch: str = typer.Option(None), job: str = typer.Option(None), max_images: int = typer.Option(None),
             style: str = typer.Option(None), aspect: str = typer.Option(None), adaptation: str = typer.Option(None),
             plan_file: Path = typer.Option(None, "--plan-file", exists=True, dir_okay=False,
                                            help="导入外部撰写的 SP输出_v3.json（跳过规划模型，仍做 v3 契约校验）"),
             confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """节点 1：读四文件，一次规划写出视频提示词和生图提示词（契约机械校验）。"""
    from .v2v import pipeline

    def go():
        ws = _ws()
        paths, inp = _v2v_job(ws, job_dir, inputs, batch, job)
        _out(pipeline.run_plan(paths, inp, _providers(ws, confirm_paid, dry_run),
                               _v2v_options(max_images, style, aspect, adaptation), external=plan_file))
    _run(go)


@v2v_app.command("images")
def v2v_images(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
               confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """节点 2：按 anchors 取帧、准备输入、逐图请求（job_id+image_id 幂等，已成功的不重复请求）。"""
    from .v2v import pipeline

    def go():
        ws = _ws()
        paths, inp = _v2v_job(ws, job_dir, None, None, None) if (job_dir / "scene.json").exists() else _v2v_from_meta(ws, job_dir)
        _out(pipeline.run_images(paths, inp, _providers(ws, confirm_paid, dry_run)))
    _run(go)


def _v2v_from_meta(ws: Workspace, job_dir: Path):
    from .v2v import planner

    paths = job_from_path(job_dir)
    folder = Path(paths.read_meta()["inputs_dir"])
    return paths, planner.inputs_from_dir(folder, paths.job_id)


@v2v_app.command("package")
def v2v_package(job_dir: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """节点 3：绑定 @图N 与实际文件，生成 项目_提交包（package_ready 仅表示文件齐全）。"""
    from .v2v import pipeline

    def go():
        ws = _ws()
        paths, inp = _v2v_job(ws, job_dir, None, None, None) if (job_dir / "scene.json").exists() else _v2v_from_meta(ws, job_dir)
        _out(pipeline.run_package(paths, inp, _providers(ws, False, False), {}))
    _run(go)


@v2v_app.command("submit")
def v2v_submit(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
               confirm_paid: bool = typer.Option(False, "--confirm-paid"), dry_run: bool = typer.Option(False, "--dry-run")) -> None:
    """提交视频生成：默认只导出手工提交说明；配置 http_generic 且确认后才真实提交。"""
    from .v2v import pipeline

    def go():
        ws = _ws()
        _out(pipeline.run_submit(job_from_path(job_dir), _providers(ws, confirm_paid, dry_run)))
    _run(go)


@v2v_app.command("compare")
def v2v_compare(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
                result: Path = typer.Option(None, exists=True, dir_okay=False, help="平台返回的成片；默认 v2v/结果/v2v_result.mp4")) -> None:
    """生成白模与 V2V 成片的并排对比视频（看板使用）。"""
    from .v2v import pipeline

    def go():
        ws = _ws()
        paths, inp = _v2v_job(ws, job_dir, None, None, None) if (job_dir / "scene.json").exists() else _v2v_from_meta(ws, job_dir)
        _out({"compare": str(pipeline.run_compare(paths, load_settings(), inp, result))})
    _run(go)


# --------------------------------------------------------------------------- review / batch / registry / cost
@review_app.command("mark")
def review_mark(job_dir: Path = typer.Argument(..., exists=True, file_okay=False),
                field: str = typer.Option(..., help="sampled_visual | normal_speed_viewing | curation"),
                value: str = typer.Option(..., help="passed/failed/not_run；curation 用 adopted/rejected/pending"),
                reviewer: str = typer.Option(..., help="复核人"), note: str = typer.Option("", help="备注")) -> None:
    """记录人工复核（唯一能改变 sampled_visual / normal_speed_viewing / curation 的方式）。"""
    from .qc import review

    def go():
        ws = _ws()
        _out(review.mark(job_from_path(job_dir), Ledger(ws.ledger_path), field, value, reviewer, note))
    _run(go)


@review_app.command("export")
def review_export(batch: str = typer.Argument(...), dataset: str = typer.Option(None, help="FiftyOne 数据集名")) -> None:
    """审片导出：每条任务一组（原片 / 白模 / V2V 成片）；装了 FiftyOne 写分组数据集，否则写可编辑的清单。"""
    from .qc import review_export as rx

    def go():
        ws = _ws()
        _out(rx.export(ws, batch, Ledger(ws.ledger_path), dataset))
    _run(go)


@review_app.command("sync")
def review_sync(batch: str = typer.Argument(...), reviewer: str = typer.Option(..., help="复核人（必填）"),
                dataset: str = typer.Option(None)) -> None:
    """把审片标签（FiftyOne 或清单里的 tags）写回台账；只处理新增标签，每条都记复核人。"""
    from .qc import review_export as rx

    def go():
        ws = _ws()
        _out(rx.sync(ws, batch, Ledger(ws.ledger_path), reviewer, dataset))
    _run(go)


@batch_app.command("finish")
def batch_finish(batch: str = typer.Argument(...), zip_: bool = typer.Option(True, "--zip/--no-zip"),
                 include_blend: bool = typer.Option(False, help="ZIP 中包含 .blend 工程"),
                 export_models: bool = typer.Option(False, help="打包前为每个已渲染任务导出 FBX/GLB 与 镜头切换.csv")) -> None:
    """写 交付清单.json / 生产与复核记录.json / 阅读说明.md，刷新批次清单，打包 ZIP（不覆盖旧包）。"""
    from .qc.finish import finish_batch

    def go():
        ws = _ws()
        _out(finish_batch(ws, Ledger(ws.ledger_path), check_id(batch, "batch id"), zip_, include_blend,
                          export_models=export_models))
    _run(go)


@registry_app.command("scan")
def registry_scan() -> None:
    """扫描工作区，重建入库目录（catalog.sqlite / .csv / .json）。"""
    from .registry import scan

    def go():
        ws = _ws()
        rows = scan(ws)
        _out({"items": len(rows), "catalog": str(ws.registry / "catalog.sqlite")})
    _run(go)


@app.command()
def dashboard(open_: bool = typer.Option(False, "--open", help="生成后用默认浏览器打开")) -> None:
    """生成本地静态看板（双路对比、筛选、搜索），无需服务器。"""
    from .registry import dashboard as build

    def go():
        ws = _ws()
        path = build(ws)
        if open_:
            import webbrowser

            webbrowser.open(path.as_uri())
        _out({"dashboard": str(path)})
    _run(go)


@cost_app.command("report")
def cost_report(batch: str = typer.Option(None), job: str = typer.Option(None, help="任务键 batch/job"),
                fmt: str = typer.Option("md", "--format", help="md | csv | json"),
                out: Path = typer.Option(None, help="输出文件；默认打印")) -> None:
    """成本报表：实际调用金额、单价未知的调用、dry-run 预估，按模型与任务汇总。"""
    from .cost.report import summarize, to_csv, to_markdown

    def go():
        ws = _ws()
        ledger = Ledger(ws.ledger_path)
        if fmt == "csv":
            path = to_csv(ledger, out or ws.registry / "cost.csv", batch_id=batch)
            _out({"csv": str(path)})
            return
        summary = summarize(ledger, batch_id=batch, job_key=job)
        text = to_markdown(summary) if fmt == "md" else json.dumps(summary, ensure_ascii=False, indent=2)
        if out:
            out.write_text(text, encoding="utf-8")
            _out({"report": str(out)})
        else:
            typer.echo(text)
    _run(go)


if __name__ == "__main__":
    app()
