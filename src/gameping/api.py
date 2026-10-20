"""FastAPI application: REST + static web UI."""
from __future__ import annotations

import io
import csv
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import WEB_DIR, load_config, save_config
from .db import Database
from .probe import ProbeTarget, resolve
from .runner import Runner
from .scheduler import Scheduler, due_now, parse_hhmm
from .stats import (Stat, baseline_delta, bucketize, compare, loss_bursts,
                    spikes, stutter_score, summarize, trend)

from . import alerts as alert_rules
from .report import build_report


class StartReq(BaseModel):
    duration: int = Field(default=300, ge=0, le=86400, description="测试时长(秒)，0 表示按轮数")
    interval: float = Field(default=5.0, ge=0, le=3600, description="每轮间隔(秒)")
    timeout: float = Field(default=1.0, gt=0, le=30, description="单次探测超时(秒)")
    rounds: int = Field(default=0, ge=0, le=100000, description="只测 N 轮，0 表示按时长")
    target_ids: list[int] | None = None
    note: str = ""


class TargetReq(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(default=443, ge=1, le=65535)
    proto: Literal["tcp", "icmp", "dns", "http"] = "tcp"
    group: str = ""
    note: str = ""


class InstanceReq(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = ""
    note: str = ""


class ScheduleReq(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    at_time: str = Field(pattern=r"^\d{1,2}:\d{2}$")
    duration: int = Field(default=300, ge=10, le=86400)
    interval: float = Field(default=5.0, ge=0.1, le=3600)
    timeout: float = Field(default=1.0, gt=0, le=30)
    target_ids: list[int] = []
    enabled: int = 1


def _dt(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now()


def now_iso_str() -> str:
    return datetime.now().isoformat(timespec="seconds")


def create_app(db_path: str | None = None) -> FastAPI:
    db = Database(db_path)
    runner = Runner(db)
    scheduler = Scheduler(db, runner)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 重启后回挂最后一次会话，界面仍能看到上一轮结果
        recent = db.list_sessions(limit=1)
        if recent and recent[0]["status"] != "running":
            try:
                runner.hydrate(int(recent[0]["id"]))
            except Exception:
                pass
        scheduler.start()
        yield
        scheduler.stop()
        runner.stop()
        db.close()

    app = FastAPI(title="GamePing", version="1.1.0",
                  description="游戏平台网络质量自动测试 · 历史存档 · 分析",
                  lifespan=lifespan)
    app.state.db = db
    app.state.runner = runner
    app.state.scheduler = scheduler

    # ==================== 静态资源 ====================
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        page = WEB_DIR / "index.html"
        if not page.exists():
            raise HTTPException(500, "web/index.html 缺失")
        return FileResponse(page)

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        icon = WEB_DIR / "favicon.svg"
        return FileResponse(icon) if icon.exists() else Response(status_code=204)

    @app.get("/sw.js", include_in_schema=False)
    def service_worker():
        """Service worker must be served from the root to control the whole scope."""
        path = WEB_DIR / "sw.js"
        if not path.exists():
            raise HTTPException(404, "sw.js 缺失")
        return FileResponse(path, media_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    @app.get("/manifest.webmanifest", include_in_schema=False)
    def manifest():
        path = WEB_DIR / "manifest.webmanifest"
        if not path.exists():
            raise HTTPException(404, "manifest 缺失")
        return FileResponse(path, media_type="application/manifest+json")

    # ==================== 状态 ====================
    @app.get("/api/health")
    def health():
        return {"ok": True, "time": datetime.now().isoformat(timespec="seconds"),
                "version": app.version}

    @app.get("/api/config")
    def get_config():
        return {"config": load_config(),
                "overview": db.stats_overview(),
                "running": runner.running}

    @app.patch("/api/config")
    def patch_config(patch: dict[str, Any]):
        return {"config": save_config(patch)}

    # ==================== 目标管理 ====================
    @app.get("/api/targets")
    def list_targets(all: bool = Query(default=False, alias="all")):
        items = db.list_targets(enabled_only=not all)
        for t in items:
            t["ip"] = resolve(t["host"], ttl=1.0)
        return {"items": items, "count": len(items)}

    @app.post("/api/targets", status_code=201)
    def add_target(req: TargetReq):
        try:
            return db.add_target(req.name, req.host, req.port, req.proto, req.group, req.note)
        except Exception as exc:
            raise HTTPException(400, f"新增目标失败：{exc}") from exc

    @app.patch("/api/targets/{target_id}")
    def patch_target(target_id: int, patch: dict[str, Any]):
        if not db.get_target(target_id):
            raise HTTPException(404, "目标不存在")
        got = db.update_target(target_id, **patch)
        return got

    @app.delete("/api/targets/{target_id}")
    def remove_target(target_id: int):
        if not db.delete_target(target_id):
            raise HTTPException(404, "目标不存在")
        return {"deleted": target_id}

    @app.post("/api/targets/{target_id}/probe")
    def probe_once(target_id: int):
        t = db.get_target(target_id)
        if not t:
            raise HTTPException(404, "目标不存在")
        from .probe import ProbeTarget
        pt = ProbeTarget(id=t["id"], name=t["name"], host=t["host"], port=t["port"],
                         proto=t["proto"])
        pt.refresh_ip()
        ok, ms = pt.probe(2.0)
        return {"ok": ok, "ms": None if ms is None else round(ms, 1), "ip": pt.ip,
                "host": t["host"], "port": t["port"], "proto": t["proto"]}

    # ==================== 测试控制 ====================
    @app.post("/api/session/start", status_code=201)
    def start_session(req: StartReq):
        if req.rounds == 0 and req.duration == 0:
            raise HTTPException(400, "时长与轮数不能同时为 0")
        try:
            return runner.start(duration=req.duration, interval=req.interval,
                                timeout=req.timeout, target_ids=req.target_ids,
                                rounds=req.rounds, note=req.note)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.post("/api/session/stop")
    def stop_session():
        return runner.stop()

    @app.get("/api/session/live")
    def live():
        state = runner.state.snapshot()
        stats = runner.live_stats() if state["session_id"] else []
        state["stats"] = stats
        # 汇总一次整体卡顿评分（只算已收满样本的目标，避免早期噪声）
        scored = [s["stutter"]["score"] for s in stats
                  if s.get("stutter") and s.get("sent", 0) >= 3]
        state["stutter_avg"] = round(sum(scored) / len(scored), 1) if scored else None
        return state

    # ==================== 历史 ====================
    @app.get("/api/sessions")
    def sessions(limit: int = Query(default=30, ge=1, le=200),
                 offset: int = Query(default=0, ge=0)):
        items = db.list_sessions(limit=limit, offset=offset)
        for s in items:
            s["id"] = int(s["id"])
        return {"items": items, "total": db.count_sessions()}

    @app.get("/api/sessions/{session_id}")
    def session_detail(session_id: int):
        s = db.get_session(session_id)
        if not s:
            raise HTTPException(404, "会话不存在")
        s["summary"] = db.session_summary(session_id)
        return s

    @app.delete("/api/sessions/{session_id}")
    def delete_session(session_id: int):
        if session_id == runner.state.session_id and runner.running:
            raise HTTPException(409, "该会话正在运行，无法删除")
        if not db.delete_session(session_id):
            raise HTTPException(404, "会话不存在")
        return {"deleted": session_id}

    @app.get("/api/sessions/{session_id}/series")
    def session_series(session_id: int, target_id: int | None = None,
                       buckets: int = Query(default=80, ge=1, le=400)):
        if not db.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        summary = db.session_summary(session_id)
        info = {row["target_id"]: row for row in summary}
        result = []
        targets = [target_id] if target_id else [r["target_id"] for r in summary]
        for tid in targets:
            samples = db.fetch_samples(session_id, target_id=tid)
            if not samples:
                continue
            pts = [(_dt(s["ts"]), s["ms"] if s["ok"] else None) for s in samples]
            # 桶数不超过样本数，避免大量空桶；每桶内含原始读数供悬停使用
            nb = min(buckets, len(samples))
            row = info.get(tid, {})
            target_row = db.get_target(tid) or {}
            vals = [s["ms"] for s in samples if s["ok"] and s["ms"] is not None]
            st = summarize(vals, len(samples), target_id=tid,
                           target=row.get("target", str(tid)),
                           host=row.get("host", ""), proto=row.get("proto", "tcp"))
            result.append({
                "target_id": tid,
                "target": row.get("target", str(tid)),
                "host": row.get("host", ""),
                "proto": row.get("proto", "tcp"),
                "group": row.get("grp", ""),
                "points": bucketize(pts, nb),
                "trend": trend(pts),
                "spikes": spikes(pts),
                "bursts": loss_bursts(pts),
                "stutter": stutter_score(pts),
                "baseline": baseline_delta(st, target_row.get("baseline")),
                "raw_count": len(samples),
            })
        return {"session_id": session_id, "series": result}

    @app.get("/api/sessions/{session_id}/compare")
    def session_compare(session_id: int, a: int, b: int):
        for tid in (a, b):
            if not db.get_target(tid):
                raise HTTPException(404, f"目标 {tid} 不存在")

        def stat_of(tid: int):
            samples = db.fetch_samples(session_id, target_id=tid)
            t = db.get_target(tid) or {}
            vals = [s["ms"] for s in samples if s["ok"] and s["ms"] is not None]
            st = summarize(vals, len(samples), target_id=tid,
                           target=t.get("name", str(tid)), host=t.get("host", ""),
                           proto=t.get("proto", "tcp"))
            d = st.as_dict()
            return d

        sa, sb = stat_of(a), stat_of(b)
        from .stats import Stat
        return {"a": sa, "b": sb,
                "compare": compare(Stat(**{k: sa[k] for k in Stat.__slots__}),
                                   Stat(**{k: sb[k] for k in Stat.__slots__}))}

    # ==================== 导出 ====================
    @app.get("/api/sessions/{session_id}/chart.svg")
    def export_chart(session_id: int, download: bool = False, title: str = ""):
        """单文件 SVG 图表，可直接发群或转 PNG。"""
        from .export import build_svg

        try:
            svg = build_svg(db, session_id, title=title)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        headers = {}
        if download:
            headers["Content-Disposition"] = \
                f'attachment; filename="gameping-chart-{session_id}.svg"'
        return Response(svg, media_type="image/svg+xml; charset=utf-8", headers=headers)

    @app.get("/api/sessions/{session_id}/export.csv")
    def export_csv(session_id: int):
        s = db.get_session(session_id)
        if not s:
            raise HTTPException(404, "会话不存在")
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["timestamp", "round", "target", "host", "protocol", "ip", "ok", "ms"])
        for r in db.fetch_samples(session_id):
            w.writerow([r["ts"], r["rnd"], r["target"], r["host"], r["proto"],
                        r["ip"], int(r["ok"]), "" if r["ms"] is None else r["ms"]])
        data = "\ufeff" + buf.getvalue()          # BOM 让 Excel 正确识别 UTF-8
        return PlainTextResponse(
            data, media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition":
                     f'attachment; filename="gameping-session-{session_id}.csv"'})

    @app.get("/api/sessions/{session_id}/export.json")
    def export_json(session_id: int):
        s = db.get_session(session_id)
        if not s:
            raise HTTPException(404, "会话不存在")
        s["summary"] = db.session_summary(session_id)
        s["samples"] = db.fetch_samples(session_id)
        return JSONResponse(s, headers={
            "Content-Disposition": f'attachment; filename="gameping-session-{session_id}.json"'})

    # ==================== 分析：跨会话 ====================
    @app.get("/api/analytics/trends")
    def analytics_trends(days: int = Query(default=30, ge=1, le=3650),
                         target_id: int | None = None,
                         note: str | None = None):
        rows = db.daily_trend(days=days, target_id=target_id, note=note)
        by_target: dict[int, dict] = {}
        for r in rows:
            slot = by_target.setdefault(r["target_id"], {
                "target_id": r["target_id"], "target": r["target"],
                "group": r.get("grp", ""), "days": []})
            slot["days"].append({"day": r["day"], "avg": r["avg"], "loss": r["loss"],
                                 "sent": r["sent"]})
        series = []
        for item in by_target.values():
            vals = [d["avg"] for d in item["days"] if d["avg"] is not None]
            first = vals[:max(1, len(vals) // 3)]
            last = vals[-max(1, len(vals) // 3):]
            delta = (sum(last) / len(last) - sum(first) / len(first)) if vals else None
            pct = (delta * 100.0 / (sum(first) / len(first))) if vals and sum(first) else None
            item["avg_overall"] = round(sum(vals) / len(vals), 1) if vals else None
            item["delta"] = round(delta, 1) if delta is not None else None
            item["delta_pct"] = round(pct, 1) if pct is not None else None
            item["direction"] = ("flat" if not pct or abs(pct) < 10
                                 else "up" if pct > 0 else "down")
            item["days_count"] = len(item["days"])
            series.append(item)
        series.sort(key=lambda x: (x["group"], x["target_id"]))
        return {"days": days, "series": series}

    @app.get("/api/analytics/heatmap")
    def analytics_heatmap(days: int = Query(default=14, ge=1, le=3650),
                          bucket: Literal["hour", "dow"] = "hour"):
        rows = db.heatmap(days=days, bucket=bucket)
        slots = 24 if bucket == "hour" else 7
        targets: dict[int, dict] = {}
        for r in rows:
            t = targets.setdefault(r["target_id"], {
                "target_id": r["target_id"], "target": r["target"],
                "cells": [None] * slots})
            t["cells"][min(slots - 1, int(r["slot"]))] = {
                "loss": r["loss"], "avg": r["avg"], "sent": r["sent"]}
        return {"days": days, "bucket": bucket, "slots": slots,
                "labels": ([f"{i:02d}" for i in range(24)] if bucket == "hour"
                           else ["日", "一", "二", "三", "四", "五", "六"]),
                "targets": sorted(targets.values(), key=lambda x: x["target_id"])}

    @app.get("/api/analytics/notes")
    def analytics_notes():
        """会话备注列表（链路标签），用于按链路筛选与对比。"""
        return {"items": db.list_notes()}

    @app.get("/api/analytics/notes/compare")
    def analytics_notes_compare(a: str, b: str):
        """两条链路（两条备注）在同一批目标上的表现对比。"""
        sa = db.note_summary(a)
        sb = db.note_summary(b)
        if not sa and not sb:
            raise HTTPException(404, "两条备注都没有数据")
        ma = {r["target_id"]: r for r in sa}
        mb = {r["target_id"]: r for r in sb}
        rows = []
        for tid in sorted(set(ma) | set(mb)):
            ra, rb = ma.get(tid), mb.get(tid)
            base = ra or rb
            delta = None
            if ra and rb and ra["avg"] is not None and rb["avg"] is not None:
                delta = round(rb["avg"] - ra["avg"], 1)
            rows.append({
                "target_id": tid, "target": base["target"], "group": base.get("grp", ""),
                "a_avg": ra["avg"] if ra else None, "a_loss": ra["loss"] if ra else None,
                "b_avg": rb["avg"] if rb else None, "b_loss": rb["loss"] if rb else None,
                "delta": delta,
            })
        return {"a": a, "b": b, "rows": rows}

    @app.get("/api/analytics/window")
    def analytics_window(start: str, end: str, target_ids: str | None = None,
                         buckets: int = Query(default=120, ge=2, le=600)):
        ids = [int(x) for x in (target_ids or "").split(",") if x.strip().isdigit()]
        rows = db.samples_between(start, end, ids or None)
        if not rows:
            return {"start": start, "end": end, "series": [], "count": 0}
        grouped: dict[int, list] = {}
        for r in rows:
            grouped.setdefault(r["target_id"], []).append(r)
        series = []
        for tid, items in grouped.items():
            pts = [(_dt(s["ts"]), s["ms"] if s["ok"] else None) for s in items]
            series.append({
                "target_id": tid, "target": items[0]["target"],
                "host": items[0]["host"], "proto": items[0]["proto"],
                "points": bucketize(pts, buckets), "trend": trend(pts),
                "spikes": spikes(pts), "raw_count": len(items),
            })
        series.sort(key=lambda x: x["target_id"])
        return {"start": start, "end": end, "series": series, "count": len(rows)}

    # ==================== 报告 ====================
    @app.get("/api/sessions/{session_id}/report.html")
    def report_html(session_id: int, download: bool = False):
        try:
            page = build_report(db, session_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        headers = {}
        if download:
            headers["Content-Disposition"] = \
                f'attachment; filename="gameping-report-{session_id}.html"'
        return Response(page, media_type="text/html; charset=utf-8", headers=headers)

    # ==================== 导入 ====================
    @app.post("/api/sessions/import")
    def import_session(payload: dict[str, Any]):
        rows = payload.get("samples") or payload.get("rows") or []
        if not isinstance(rows, list) or not rows:
            raise HTTPException(400, "samples 不能为空")
        try:
            # 备注只保留调用方真正给出的值；instance 用于多机汇总来源
            return db.import_samples(rows,
                                     note=str(payload.get("note") or ""),
                                     instance=str(payload.get("instance") or ""),
                                     label=str(payload.get("label") or ""))
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"导入失败：{exc}") from exc

    @app.post("/api/sessions/import.csv")
    def import_csv(body: str = Body(..., media_type="text/csv")):
        reader = csv.DictReader(io.StringIO(body.lstrip("\ufeff")))
        rows = []
        for raw in reader:
            norm = {k.strip().lower(): v for k, v in raw.items() if k}
            rows.append({
                "ts": norm.get("timestamp") or norm.get("ts") or "",
                "target": norm.get("target") or norm.get("target_name") or "",
                "ok": norm.get("ok", "1"),
                "ms": norm.get("ms"),
                "ip": norm.get("ip", ""),
            })
        if not rows:
            raise HTTPException(400, "CSV 中没有数据行")
        try:
            return db.import_samples(rows, note="")
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, f"导入失败：{exc}") from exc

    # ==================== 定时任务 ====================
    @app.get("/api/schedules")
    def list_schedules():
        return {"items": db.list_schedules(),
                "running": scheduler.running,
                "last_error": scheduler.last_error,
                "fired": scheduler.fired[:10]}

    @app.post("/api/schedules", status_code=201)
    def add_schedule(req: ScheduleReq):
        try:
            parse_hhmm(req.at_time)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return db.add_schedule(req.name, req.at_time, req.duration, req.interval,
                               req.timeout, req.target_ids)

    @app.patch("/api/schedules/{schedule_id}")
    def patch_schedule(schedule_id: int, patch: dict[str, Any]):
        if not db.get_schedule(schedule_id):
            raise HTTPException(404, "计划不存在")
        if "at_time" in patch:
            try:
                parse_hhmm(patch["at_time"])
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return db.update_schedule(schedule_id, **patch)

    @app.delete("/api/schedules/{schedule_id}")
    def remove_schedule(schedule_id: int):
        if not db.delete_schedule(schedule_id):
            raise HTTPException(404, "计划不存在")
        return {"deleted": schedule_id}

    @app.post("/api/schedules/{schedule_id}/run")
    def run_schedule_now(schedule_id: int):
        sch = db.get_schedule(schedule_id)
        if not sch:
            raise HTTPException(404, "计划不存在")
        ids = [int(x) for x in (sch["target_ids"] or "").split(",") if x.strip()]
        try:
            state = runner.start(duration=int(sch["duration"]), interval=float(sch["interval"]),
                                 timeout=float(sch["timeout"]), target_ids=ids or None,
                                 note=f"手动触发:{sch['name']}")
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        db.update_schedule(schedule_id, last_run_at=now_iso_str(),
                           last_session_id=state["session_id"])
        return state

    # ==================== 告警 ====================
    @app.get("/api/alerts")
    def list_alerts(limit: int = Query(default=50, ge=1, le=500),
                    unseen_only: bool = False):
        return {"items": db.list_alerts(limit=limit, unseen_only=unseen_only),
                "unseen": db.stats_overview()["unseen_alerts"],
                "thresholds": load_config().get("thresholds")
                or alert_rules.DEFAULT_THRESHOLDS}

    @app.post("/api/alerts/seen")
    def mark_alerts(alert_id: int | None = None):
        return {"updated": db.mark_alerts_seen(alert_id)}

    @app.post("/api/alerts/check/{session_id}")
    def check_alerts(session_id: int):
        if not db.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        return {"created": alert_rules.check_session(db, session_id)}

    # ==================== 维护 ====================
    @app.post("/api/maintenance/purge")
    def purge(days: int = Query(default=0, ge=0, le=3650)):
        removed = db.purge_older_than(days)
        return {"removed_sessions": removed, "days": days}

    # ==================== 多机汇总 ====================
    @app.get("/api/instances")
    def list_instances():
        return {"items": db.list_instances()}

    @app.post("/api/instances", status_code=201)
    def upsert_instance(req: InstanceReq):
        return db.touch_instance(req.id, label=req.label, note=req.note)

    @app.delete("/api/instances/{instance_id}")
    def remove_instance(instance_id: str):
        if not db.delete_instance(instance_id):
            raise HTTPException(404, "实例不存在")
        return {"deleted": instance_id}

    # ==================== DNS 解析诊断 ====================
    @app.get("/api/dns/check")
    def dns_check(host: str | None = None, target_id: int | None = None):
        """解析目标并报告所有地址、是否疑似劫持、有无 IPv6。"""
        from .probe import resolve_all

        if target_id is not None:
            t = db.get_target(target_id)
            if not t:
                raise HTTPException(404, "目标不存在")
            host = t["host"]
        if not host:
            raise HTTPException(400, "需要 host 或 target_id")
        info = resolve_all(host)
        info["host"] = host
        # 该主机在库里最近一次实际使用的地址族
        return info

    @app.get("/api/dns/all")
    def dns_all():
        """批量解析所有启用目标，一次看清哪个域名被污染或没有 IPv6。"""
        from .probe import resolve_all

        out = []
        for t in db.list_targets():
            info = resolve_all(t["host"])
            out.append({
                "target_id": t["id"], "target": t["name"], "host": t["host"],
                "v4": info["v4"], "v6": info["v6"],
                "suspicious": info["suspicious"], "reason": info["reason"],
                "ms": info["ms"],
            })
        return {"items": out, "count": len(out)}

    # ==================== 卡顿分析 ====================
    @app.get("/api/sessions/{session_id}/stutter")
    def session_stutter(session_id: int):
        """整段会话的卡顿评分、丢包突发与体感问题清单。"""
        if not db.get_session(session_id):
            raise HTTPException(404, "会话不存在")
        out = []
        for row in db.session_summary(session_id):
            tid = row["target_id"]
            samples = db.fetch_samples(session_id, target_id=tid)
            pts = [(_dt(s["ts"]), s["ms"] if s["ok"] else None) for s in samples]
            target_row = db.get_target(tid) or {}
            vals = [s["ms"] for s in samples if s["ok"] and s["ms"] is not None]
            st = summarize(vals, len(samples), target_id=tid, target=row["target"],
                           host=row["host"], proto=row["proto"])
            out.append({
                "target_id": tid, "target": row["target"], "group": row.get("grp", ""),
                "stutter": stutter_score(pts),
                "bursts": loss_bursts(pts),
                "baseline": baseline_delta(st, target_row.get("baseline")),
                "avg": st.avg, "loss": st.loss, "jitter": st.jitter,
            })
        out.sort(key=lambda x: x["stutter"]["score"])
        return {"session_id": session_id, "items": out,
                "worst": out[0]["target"] if out else None}

    # ==================== 多机汇总 ====================
    @app.get("/api/stats/overview")
    def overview():
        return db.stats_overview()

    return app


app = create_app()
