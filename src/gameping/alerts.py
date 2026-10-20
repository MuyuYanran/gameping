"""Threshold alerts evaluated when a session finishes."""
from __future__ import annotations

from typing import Any

from .db import Database

# 默认阈值，可通过 config 覆盖
DEFAULT_THRESHOLDS = {"latency": 100.0, "loss": 5.0, "jitter": 50.0}


def check_session(db: Database, session_id: int,
                  thresholds: dict[str, float] | None = None) -> list[dict[str, Any]]:
    """Compare one session's per-target summary against thresholds; store alerts."""
    th = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    created: list[dict[str, Any]] = []
    for row in db.session_summary(session_id):
        avg, loss = row.get("avg"), float(row.get("loss") or 0.0)
        if avg is not None and avg > th["latency"]:
            created.append(_emit(db, session_id, row, "latency", avg, th["latency"],
                                 f"平均延迟 {avg:.1f}ms 超过阈值 {th['latency']:.0f}ms"))
        if loss > th["loss"]:
            created.append(_emit(db, session_id, row, "loss", loss, th["loss"],
                                 f"丢包率 {loss:.1f}% 超过阈值 {th['loss']:.0f}%"))
    # 抖动需要逐目标算标准差，session_summary 不含，这里单独查
    for row in db.session_summary(session_id):
        if row.get("avg") is None:
            continue
        samples = db.fetch_samples(session_id, target_id=row["target_id"])
        vals = [s["ms"] for s in samples if s["ok"] and s["ms"] is not None]
        if len(vals) < 2:
            continue
        mean = sum(vals) / len(vals)
        jitter = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
        if jitter > th["jitter"]:
            created.append(_emit(db, session_id, row, "jitter", jitter, th["jitter"],
                                 f"抖动 {jitter:.1f}ms 超过阈值 {th['jitter']:.0f}ms"))
    return created


def _emit(db: Database, session_id: int, row: dict, kind: str, value: float,
          threshold: float, message: str) -> dict[str, Any]:
    alert_id = db.add_alert(row["target"], kind, value, threshold,
                            target_id=row.get("target_id"), session_id=session_id,
                            message=message)
    return {"id": alert_id, "target": row["target"], "kind": kind,
            "value": round(value, 1), "threshold": threshold, "message": message}


__all__ = ["check_session", "DEFAULT_THRESHOLDS"]
