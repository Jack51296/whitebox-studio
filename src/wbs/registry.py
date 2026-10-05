"""Catalog of every job (SQLite + CSV + JSON) and a static dual-comparison dashboard (local HTML, no server)."""

from __future__ import annotations

import csv
import html
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .cost.report import summarize
from .jsonio import read_json, write_json, write_text
from .layout import JobPaths, Workspace
from .ledger import Ledger

COLUMNS = ["key", "batch", "job_id", "route", "title", "status", "technical", "sampled_visual", "normal_speed_viewing",
           "curation", "static_gate", "pregate", "duration_s", "shots", "content_class", "subject", "camera_move", "era",
           "video", "overview", "reference_video", "compare_video", "v2v_plan", "v2v_package_ready", "v2v_submit",
           "cost_cny", "updated_at"]


def _rel(path: Path | None, base: Path) -> str | None:
    if path is None or not path.exists():
        return None
    return os.path.relpath(path, base).replace("\\", "/")


def job_row(job: JobPaths, ws: Workspace, costs: dict[str, Any]) -> dict[str, Any]:
    meta = job.read_meta()
    scene = read_json(job.scene) if job.scene.exists() else {}
    summary = read_json(job.report("质检汇总.json")) if job.report("质检汇总.json").exists() else {}
    labels = (scene.get("meta") or {}).get("labels") or {}
    video = job.video(scene["title"]) if scene else job.v2v_dir / "项目_提交包" / "视频1_白模.mp4"
    overview = job.overview
    if not scene:
        boards = sorted(p for p in (job.v2v_dir / "四文件").glob("*") if "分镜总览" in p.stem or "故事板拼版" in p.stem) \
            if (job.v2v_dir / "四文件").exists() else []
        overview = boards[0] if boards else overview
    reference = job.root / meta["source_video"] if meta.get("source_video") else None
    compare = job.v2v_dir / "双路对比.mp4"
    result = job.v2v_dir / "结果" / "v2v_result.mp4"
    cost = costs.get(job.key, {})
    return {
        "key": job.key, "batch": job.batch_id, "job_id": job.job_id, "route": meta.get("route", ""),
        "title": scene.get("title", meta.get("title", job.job_id)), "status": meta.get("status", ""),
        "technical": summary.get("technical", meta.get("qc_status", "not_run")),
        "sampled_visual": summary.get("sampled_visual", "not_run"),
        "normal_speed_viewing": summary.get("normal_speed_viewing", "not_run"),
        "curation": summary.get("curation", "pending"), "static_gate": meta.get("static_gate", "not_applicable"),
        "pregate": meta.get("pregate", ""), "duration_s": scene.get("duration_s"), "shots": len(scene.get("shots", [])),
        "content_class": labels.get("content_class_label") or (scene.get("meta") or {}).get("content_class_label", ""),
        "subject": labels.get("subject_label", ""), "camera_move": labels.get("camera_move_label", ""),
        "era": labels.get("era_label", ""),
        "video": _rel(video, ws.root), "overview": _rel(overview, ws.root), "reference_video": _rel(reference, ws.root),
        "result_video": _rel(result, ws.root), "compare_video": _rel(compare, ws.root),
        "v2v_plan": meta.get("v2v_plan", ""), "v2v_package_ready": meta.get("v2v_package_ready"),
        "v2v_submit": meta.get("v2v_submit", ""), "cost_cny": cost.get("cost_cny", 0.0),
        "unknown_cost_calls": cost.get("unknown_cost_calls", 0), "updated_at": meta.get("updated_at", ""),
    }


def scan(ws: Workspace) -> list[dict[str, Any]]:
    ledger = Ledger(ws.ledger_path)
    costs = summarize(ledger)["by_job"]
    rows = [job_row(job, ws, costs) for job in ws.iter_jobs()]
    ws.registry.mkdir(parents=True, exist_ok=True)
    db = ws.registry / "catalog.sqlite"
    conn = sqlite3.connect(db)
    try:
        conn.execute("DROP TABLE IF EXISTS catalog")
        conn.execute(f"CREATE TABLE catalog ({', '.join(c + ' TEXT' for c in COLUMNS)}, PRIMARY KEY (key))")
        conn.executemany(f"INSERT INTO catalog VALUES ({', '.join('?' for _ in COLUMNS)})",
                         [[None if r.get(c) is None else str(r.get(c)) for c in COLUMNS] for r in rows])
        conn.commit()
    finally:
        conn.close()
    with (ws.registry / "catalog.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    write_json(ws.registry / "catalog.json", {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "items": rows})
    return rows


DASHBOARD = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>白模生产看板</title>
<style>
body{font-family:"Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif;margin:0;background:#f4f5f7;color:#1d2129}
header{padding:14px 20px;background:#1f2937;color:#fff}header h1{margin:0;font-size:20px}header p{margin:4px 0 0;color:#cbd5e1;font-size:13px}
.bar{display:flex;flex-wrap:wrap;gap:8px;padding:12px 20px;background:#fff;border-bottom:1px solid #e5e7eb;position:sticky;top:0;z-index:2}
.bar input,.bar select{padding:6px 8px;border:1px solid #cbd5e1;border-radius:6px;font-size:13px}
.stats{padding:8px 20px;font-size:13px;color:#4b5563}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(520px,1fr));gap:14px;padding:0 20px 24px}
.card{background:#fff;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:12px}
.card h3{margin:0 0 6px;font-size:15px}.meta{font-size:12px;color:#6b7280;margin-bottom:6px}
.badges span{display:inline-block;font-size:11px;padding:2px 6px;border-radius:4px;margin:0 4px 4px 0;background:#e5e7eb}
.passed,.adopted{background:#d1fae5!important;color:#065f46}.failed,.rejected{background:#fee2e2!important;color:#991b1b}
.not_run,.pending,.not_applicable{background:#f3f4f6!important;color:#6b7280}
.duo{display:grid;grid-template-columns:1fr 1fr;gap:6px}.duo figure{margin:0}.duo figcaption{font-size:11px;color:#6b7280}
video,img{width:100%;border-radius:6px;background:#111}.ctl button{margin:6px 6px 0 0;font-size:12px}
a{color:#2563eb;font-size:12px;margin-right:8px}
</style></head><body>
<header><h1>白模生产看板</h1><p>生成于 __GENERATED__ · 共 __COUNT__ 条 · 状态如实记录：technical 为自动检查，其余三项只由人工标记</p></header>
<div class="bar">
<input id="q" placeholder="搜索标题 / 任务 / 主体 / 运镜" size="28">
<select id="route"><option value="">全部路线</option></select>
<select id="batch"><option value="">全部批次</option></select>
<select id="tech"><option value="">technical：全部</option><option>passed</option><option>failed</option><option>not_run</option></select>
<select id="nsv"><option value="">正常速度观看：全部</option><option>passed</option><option>failed</option><option>not_run</option></select>
<select id="cur"><option value="">采用：全部</option><option>adopted</option><option>rejected</option><option>pending</option></select>
</div>
<div class="stats" id="stats"></div>
<div class="grid" id="grid"></div>
<script>
const DATA = __DATA__;
const $ = id => document.getElementById(id);
function opts(sel, key){[...new Set(DATA.map(d=>d[key]).filter(Boolean))].sort().forEach(v=>{const o=document.createElement('option');o.textContent=v;sel.appendChild(o);});}
opts($('route'),'route'); opts($('batch'),'batch');
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
function media(path, label){ if(!path) return `<figure><figcaption>${label}：无</figcaption></figure>`;
  const src = '../'+path; return path.endsWith('.mp4') ? `<figure><video src="${src}" controls preload="metadata" muted></video><figcaption>${label}</figcaption></figure>`
  : `<figure><img src="${src}"><figcaption>${label}</figcaption></figure>`; }
function card(d){
  const left = d.route==='reverse' ? media(d.reference_video,'原片（授权素材）') : media(d.video,'白模参考');
  const right = d.route==='reverse' ? media(d.video,'反推白模') : (d.result_video ? media(d.result_video,'V2V 成片') : media(d.overview,'分镜总览'));
  const b = k => `<span class="${esc(d[k])}">${k}: ${esc(d[k])}</span>`;
  return `<div class="card"><h3>${esc(d.title)}</h3>
  <div class="meta">${esc(d.key)} · ${esc(d.route)} · ${d.duration_s??'-'}s · ${d.shots} 镜 · ${esc(d.subject)} ${esc(d.camera_move)} · 费用 ¥${d.cost_cny}${d.unknown_cost_calls?'（'+d.unknown_cost_calls+' 次单价未知）':''}</div>
  <div class="badges">${b('technical')}${b('sampled_visual')}${b('normal_speed_viewing')}${b('curation')}<span class="${esc(d.static_gate)}">static_gate: ${esc(d.static_gate)}</span><span>v2v: ${esc(d.v2v_plan||'-')} / ready=${d.v2v_package_ready}</span></div>
  <div class="duo">${left}${right}</div>
  <div class="ctl"><button onclick="sync(this,'play')">▶ 同步播放</button><button onclick="sync(this,'pause')">❚❚ 暂停</button><button onclick="sync(this,'reset')">⟲ 回到开头</button>
  ${d.compare_video?`<a href="../${d.compare_video}">并排对比视频</a>`:''}<a href="../batches/${esc(d.batch)}/${esc(d.job_id)}/reports/质检汇总.json">质检汇总</a><a href="../batches/${esc(d.batch)}/${esc(d.job_id)}/剧本与分镜导演卡.txt">导演卡</a></div></div>`;}
function sync(btn, action){const vids=btn.closest('.card').querySelectorAll('video');
  vids.forEach(v=>{ if(action==='play'){v.currentTime=vids[0].currentTime;v.play();} else if(action==='pause'){v.pause();} else {v.pause();v.currentTime=0;} });}
function render(){const q=$('q').value.trim().toLowerCase();
  const rows=DATA.filter(d=>(!q||[d.title,d.key,d.subject,d.camera_move].join(' ').toLowerCase().includes(q))
    &&(!$('route').value||d.route===$('route').value)&&(!$('batch').value||d.batch===$('batch').value)
    &&(!$('tech').value||d.technical===$('tech').value)&&(!$('nsv').value||d.normal_speed_viewing===$('nsv').value)
    &&(!$('cur').value||d.curation===$('cur').value));
  $('stats').textContent=`显示 ${rows.length} / ${DATA.length} 条 · technical passed ${rows.filter(d=>d.technical==='passed').length} · 正常速度观看已做 ${rows.filter(d=>d.normal_speed_viewing!=='not_run').length} · 已采用 ${rows.filter(d=>d.curation==='adopted').length}`;
  $('grid').innerHTML=rows.map(card).join('');}
['q','route','batch','tech','nsv','cur'].forEach(id=>$(id).addEventListener('input',render)); render();
</script></body></html>
"""


def dashboard(ws: Workspace, rows: list[dict[str, Any]] | None = None) -> Path:
    rows = rows if rows is not None else scan(ws)
    out = ws.registry / "index.html"
    data = json.dumps(rows, ensure_ascii=False).replace("</", "<\\/")
    page = (DASHBOARD.replace("__DATA__", data).replace("__COUNT__", str(len(rows)))
            .replace("__GENERATED__", html.escape(time.strftime("%Y-%m-%d %H:%M:%S"))))
    write_text(out, page)
    return out
