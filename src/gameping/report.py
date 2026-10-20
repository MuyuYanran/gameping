"""Self-contained HTML report: one file, inline SVG, no external assets."""
from __future__ import annotations

import html
import json
from datetime import datetime
from typing import Any

from .db import Database
from .stats import Stat, bucketize, spikes, summarize, trend


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _grade_color(grade: str) -> str:
    return {"S": "#2dd4a7", "A": "#52c07a", "B": "#e8c341",
            "C": "#f08a3c", "X": "#ef5a6f"}.get(grade, "#8b9ab0")


def _series_svg(points: list[dict], width: int = 900, height: int = 240) -> str:
    vals = [p["avg"] for p in points if p.get("avg") is not None]
    if not vals:
        return '<p class="muted">该目标全部探测失败，无曲线可绘。</p>'
    maxv = max(max(vals) * 1.15, 20)
    n = max(len(points), 2)
    padl, padr, padt, padb = 44, 12, 12, 24
    def x(i: int) -> float:
        return padl + (width - padl - padr) * (i / (n - 1))
    def y(v: float) -> float:
        return height - padb - (height - padt - padb) * (v / maxv)
    path, pen = [], False
    for i, p in enumerate(points):
        v = p.get("avg")
        if v is None:
            pen = False
            continue
        path.append(f"{'L' if pen else 'M'}{x(i):.1f},{y(v):.1f}")
        pen = True
    grid = []
    for k in range(5):
        v = maxv * k / 4
        grid.append(f'<line x1="{padl}" y1="{y(v):.1f}" x2="{width - padr}" y2="{y(v):.1f}"'
                    f' stroke="#1b2739"/>')
        grid.append(f'<text x="{padl - 6}" y="{y(v) + 3.5:.1f}" text-anchor="end"'
                    f' font-size="10" fill="#5c6b82">{v:.0f}</text>')
    lost = "".join(
        f'<circle cx="{x(i):.1f}" cy="{height - padb}" r="2.2" fill="#ef5a6f"/>'
        for i, p in enumerate(points) if (p.get("loss") or 0) > 0)
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" height="{height}"'
            f' preserveAspectRatio="none">{"".join(grid)}'
            f'<path d="{" ".join(path)}" fill="none" stroke="#35d0ba" stroke-width="1.8"/>'
            f'{lost}</svg>')


def build_report(db: Database, session_id: int) -> str:
    sess = db.get_session(session_id)
    if not sess:
        raise KeyError(f"会话 {session_id} 不存在")
    summary = db.session_summary(session_id)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    blocks: list[str] = []
    for row in summary:
        samples = db.fetch_samples(session_id, target_id=row["target_id"])
        pts = [(datetime.fromisoformat(s["ts"]), s["ms"] if s["ok"] else None)
               for s in samples]
        vals = [s["ms"] for s in samples if s["ok"] and s["ms"] is not None]
        st: Stat = summarize(vals, len(samples), target_id=row["target_id"],
                             target=row["target"], host=row["host"], proto=row["proto"])
        tr = trend(pts)
        buckets = bucketize(pts, 70)
        direction = {"up": "↗ 变慢", "down": "↘ 变快", "flat": "→ 平稳"}[tr["direction"]]
        color = _grade_color(st.grade)
        blocks.append(f"""
        <section class="card">
          <header>
            <h2>{_esc(row['target'])} <span class="badge" style="background:{color}">{st.grade}</span></h2>
            <span class="muted mono">{_esc(row['host'])} · {_esc(row['proto'].upper())}</span>
          </header>
          <div class="kpis">
            <div><b>{st.avg if st.avg is not None else '—'}</b><span>平均 (ms)</span></div>
            <div><b>{st.med if st.med is not None else '—'}</b><span>中位</span></div>
            <div><b>{st.p95 if st.p95 is not None else '—'}</b><span>P95</span></div>
            <div><b>{st.jitter if st.jitter is not None else '—'}</b><span>抖动</span></div>
            <div><b>{st.loss:.1f}%</b><span>丢包</span></div>
            <div><b>{len(samples)}</b><span>采样</span></div>
            <div><b>{direction}</b><span>趋势</span></div>
          </div>
          {_series_svg(buckets)}
          <p class="muted small">红点为丢包时刻 · 趋势对比前 1/3 与后 1/3 均值</p>
        </section>""")

    worst = [r for r in summary if r.get("avg") is not None]
    best_target = min(worst, key=lambda r: r["avg"])["target"] if worst else "—"
    worst_target = max(worst, key=lambda r: r["avg"])["target"] if worst else "—"
    total_sent = sum(int(r["sent"]) for r in summary)
    total_recv = sum(int(r["recv"]) for r in summary)
    overall_loss = (total_sent - total_recv) * 100.0 / total_sent if total_sent else 0.0
    raw = json.dumps({"session": sess["id"], "summary": summary},
                     ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>GamePing 报告 · 会话 #{sess['id']}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#0b0f17;color:#e6edf6;font:14px/1.6 "Segoe UI","Microsoft YaHei",system-ui,sans-serif}}
.wrap{{max-width:1000px;margin:0 auto;padding:28px 20px 60px}}
h1{{font-size:23px;margin:0 0 4px}}
h2{{font-size:15px;margin:0}}
.muted{{color:#8b9ab0}} .small{{font-size:12px}} .mono{{font-family:ui-monospace,Consolas,monospace}}
.tiny{{font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:#5c6b82}}
.top{{display:flex;flex-wrap:wrap;gap:14px;margin:18px 0 22px}}
.metric{{background:#131b26;border:1px solid #223044;border-radius:11px;padding:12px 16px;min-width:130px}}
.metric b{{display:block;font-size:20px}}
.metric span{{font-size:12px;color:#5c6b82}}
.card{{background:#131b26;border:1px solid #223044;border-radius:12px;padding:16px 18px;margin-bottom:14px}}
.card header{{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:10px}}
.badge{{font-size:11px;font-weight:700;color:#062e26;padding:2px 7px;border-radius:6px;margin-left:4px}}
.kpis{{display:flex;flex-wrap:wrap;gap:16px;margin-bottom:8px}}
.kpis div{{min-width:64px}} .kpis b{{display:block;font-size:16px}}
.kpis span{{font-size:11px;color:#5c6b82}}
footer{{margin-top:26px;padding-top:14px;border-top:1px solid #1a2534;font-size:12px;color:#5c6b82}}
</style></head><body><div class="wrap">
<h1>GamePing 网络质量报告</h1>
<p class="muted">会话 #{sess['id']} · 开始 {_esc(sess['started_at'])} ·
完成 {_esc(sess.get('finished_at') or '—')} · 共 {sess['rounds_done']} 轮 ·
生成于 {generated}</p>
<div class="top">
  <div class="metric"><b>{len(summary)}</b><span>测试目标</span></div>
  <div class="metric"><b>{total_sent}</b><span>探测次数</span></div>
  <div class="metric"><b>{overall_loss:.1f}%</b><span>综合丢包</span></div>
  <div class="metric"><b>{_esc(best_target)}</b><span>最快目标</span></div>
  <div class="metric"><b>{_esc(worst_target)}</b><span>最慢目标</span></div>
</div>
{''.join(blocks)}
<footer>由 GamePing 生成 · 评级：S≤40ms / A≤70ms / B≤110ms / C&gt;110ms，丢包与抖动会降级</footer>
</div>
<script type="application/json" id="gameping-data">{raw}</script>
</body></html>"""


__all__ = ["build_report"]
