"""Standalone SVG chart export: one image per session, no JS or dependencies.

The SVG is hand-written so it renders identically in a browser, in an <img>, or
after being converted to PNG by any external tool.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Sequence
from xml.sax.saxutils import escape

from .db import Database
from .stats import bucketize, summarize

PALETTE = ["#35d0ba", "#4c8dff", "#e8c341", "#f08a3c", "#a78bfa",
           "#ef5a6f", "#52c07a", "#22d3ee", "#fb7185", "#facc15",
           "#94a3b8", "#c084fc"]

W, H = 1200, 640
PAD = {"l": 74, "r": 28, "t": 96, "b": 96}


def _grade_color(grade: str) -> str:
    return {"S": "#2dd4a7", "A": "#52c07a", "B": "#e8c341",
            "C": "#f08a3c", "X": "#ef5a6f"}.get(grade, "#8b9ab0")


def build_svg(db: Database, session_id: int, *, title: str = "") -> str:
    sess = db.get_session(session_id)
    if not sess:
        raise KeyError(f"会话 {session_id} 不存在")

    summary = db.session_summary(session_id)
    series: list[dict[str, Any]] = []
    for row in summary:
        samples = db.fetch_samples(session_id, target_id=row["target_id"])
        if not samples:
            continue
        pts = [(datetime.fromisoformat(s["ts"]), s["ms"] if s["ok"] else None)
               for s in samples]
        vals = [s["ms"] for s in samples if s["ok"] and s["ms"] is not None]
        st = summarize(vals, len(samples), target_id=row["target_id"],
                       target=row["target"], host=row["host"], proto=row["proto"])
        series.append({"row": row, "stat": st, "points": bucketize(pts, min(120, len(samples)))})

    if not series:
        raise ValueError("该会话没有可绘制的样本")

    all_vals = [p["avg"] for s in series for p in s["points"] if p["avg"] is not None]
    max_v = max(max(all_vals) * 1.15, 20.0)
    n = max(len(s["points"]) for s in series)
    plot_w = W - PAD["l"] - PAD["r"]
    plot_h = H - PAD["t"] - PAD["b"]

    def x_of(i: int) -> float:
        return PAD["l"] + plot_w * (0 if n <= 1 else i / (n - 1))

    def y_of(v: float) -> float:
        return PAD["t"] + plot_h - plot_h * (v / max_v)

    parts: list[str] = []
    parts.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="Segoe UI,Microsoft YaHei,sans-serif">')
    parts.append(f'<rect width="{W}" height="{H}" fill="#0b0f17"/>')

    caption = escape(title or f"GamePing · 会话 #{session_id}")
    parts.append(f'<text x="{PAD["l"]}" y="42" fill="#e6edf6" font-size="22" '
                 f'font-weight="700">{caption}</text>')
    when = escape(str(sess.get("started_at") or ""))
    total_sent = sum(int(r["sent"]) for r in summary)
    total_recv = sum(int(r["recv"]) for r in summary)
    loss = (total_sent - total_recv) * 100.0 / total_sent if total_sent else 0.0
    parts.append(
        f'<text x="{PAD["l"]}" y="66" fill="#8b9ab0" font-size="13">'
        f'{when} · {sess["rounds_done"]} 轮 · {total_sent} 次探测 · '
        f'综合丢包 {loss:.1f}% · {len(summary)} 个目标</text>')

    # 网格与 Y 轴刻度
    for k in range(6):
        v = max_v * k / 5
        y = y_of(v)
        parts.append(f'<line x1="{PAD["l"]}" y1="{y:.1f}" x2="{W - PAD["r"]}" '
                     f'y2="{y:.1f}" stroke="#1b2739"/>')
        parts.append(f'<text x="{PAD["l"] - 10}" y="{y + 4:.1f}" fill="#5c6b82" '
                     f'font-size="11" text-anchor="end">{v:.0f}</text>')
    parts.append(f'<text x="{PAD["l"] - 10}" y="{PAD["t"] - 12}" fill="#5c6b82" '
                 f'font-size="11" text-anchor="end">ms</text>')
    parts.append(f'<line x1="{PAD["l"]}" y1="{H - PAD["b"]}" x2="{W - PAD["r"]}" '
                 f'y2="{H - PAD["b"]}" stroke="#26364c"/>')

    # 折线
    for idx, item in enumerate(series):
        color = PALETTE[idx % len(PALETTE)]
        live = [(x_of(i), y_of(p["avg"]))
                for i, p in enumerate(item["points"]) if p["avg"] is not None]
        if len(live) > 1:
            d = " ".join(f'{"L" if i else "M"}{px:.1f},{py:.1f}'
                         for i, (px, py) in enumerate(live))
            parts.append(f'<path d="{d}" fill="none" stroke="{color}" '
                         f'stroke-width="2" stroke-linejoin="round"/>')
        elif len(live) == 1:
            parts.append(f'<circle cx="{live[0][0]:.1f}" cy="{live[0][1]:.1f}" '
                         f'r="3" fill="{color}"/>')
        # 丢包点贴在基线上
        for i, p in enumerate(item["points"]):
            if (p.get("loss") or 0) > 0:
                parts.append(f'<circle cx="{x_of(i):.1f}" cy="{H - PAD["b"] - 3}" '
                             f'r="2.6" fill="#ef5a6f"/>')

    # 时间轴首尾
    first_t = series[0]["points"][0].get("t")
    last_t = series[0]["points"][-1].get("t")

    def fmt(ts: float | None) -> str:
        if not ts:
            return ""
        d = datetime.fromtimestamp(ts)
        return f"{d.month:02d}/{d.day:02d} {d.hour:02d}:{d.minute:02d}"

    parts.append(f'<text x="{PAD["l"]}" y="{H - PAD["b"] + 22}" fill="#5c6b82" '
                 f'font-size="11">{fmt(first_t)}</text>')
    parts.append(f'<text x="{W - PAD["r"]}" y="{H - PAD["b"] + 22}" fill="#5c6b82" '
                 f'font-size="11" text-anchor="end">{fmt(last_t)}</text>')

    # 图例：两列排布，避免互相压字
    cols = 2
    per_col = (len(series) + cols - 1) // cols
    lx0, ly0 = PAD["l"], H - PAD["b"] + 44
    for i, item in enumerate(series):
        col, row = divmod(i, per_col)
        lx = lx0 + col * 540
        ly = ly0 + row * 22
        st = item["stat"]
        parts.append(f'<rect x="{lx}" y="{ly - 9}" width="12" height="3.5" rx="1.5" '
                     f'fill="{PALETTE[i % len(PALETTE)]}"/>')
        parts.append(
            f'<text x="{lx + 19}" y="{ly}" fill="#e6edf6" font-size="12">'
            f'{escape(str(item["row"]["target"]))}</text>')
        parts.append(
            f'<text x="{lx + 19 + 11 * len(str(item["row"]["target"])) + 8}" '
            f'y="{ly}" fill="#8b9ab0" font-size="12">'
            f'{"" if st.avg is None else f"{st.avg:.0f}ms"} · 丢 {st.loss:.0f}%</text>')
        parts.append(
            f'<text x="{lx + 19 + 11 * len(str(item["row"]["target"])) + 130}" '
            f'y="{ly}" fill="{_grade_color(st.grade)}" font-size="12" '
            f'font-weight="700">{st.grade}</text>')

    parts.append(f'<text x="{W - PAD["r"]}" y="{H - 12}" fill="#5c6b82" '
                 f'font-size="10" text-anchor="end">Generated by GamePing · '
                 f'{datetime.now():%Y-%m-%d %H:%M}</text>')
    parts.append("</svg>")
    return "".join(parts)


__all__ = ["build_svg"]
