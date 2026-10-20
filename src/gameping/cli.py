"""Command line entry point: serve the web UI or run a headless test."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DB_PATH, WEB_DIR, load_config


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn
    from .api import create_app

    app = create_app(args.db)
    print(f"GamePing Web UI → http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _cmd_test(args: argparse.Namespace) -> int:
    """Headless run that prints a table and stores into the same database."""
    from .config import DEFAULT_TARGETS
    from .db import Database
    from .runner import Runner

    cfg = load_config()
    db = Database(args.db)
    runner = Runner(db)
    ids = None
    if args.targets:
        wanted = {n.strip() for n in args.targets.split(",") if n.strip()}
        ids = [t["id"] for t in db.list_targets()
               if t["name"] in wanted or t["host"] in wanted or str(t["id"]) in wanted]
        if not ids:
            print(f"未匹配到目标：{args.targets}", file=sys.stderr)
            print("可用目标：", file=sys.stderr)
            for t in db.list_targets():
                print(f"  {t['id']:>2}  {t['name']:<14} {t['host']}:{t['port']}",
                      file=sys.stderr)
            return 2

    runner.start(duration=args.duration, interval=args.interval, timeout=args.timeout,
                 target_ids=ids, rounds=args.rounds, note="CLI")
    try:
        runner.wait()
    except KeyboardInterrupt:
        print("\n正在停止 ...")
        runner.stop()
        runner.wait(3)

    rows = runner.live_stats()
    print()
    _print_table(rows)
    print(f"\n已写入数据库：{db.path}")
    return 0


def _print_table(rows: list[dict]) -> None:
    if not rows:
        print("没有数据")
        return
    head = ["目标", "协议", "地址", "次数", "丢包%", "最小", "平均", "中位", "P95", "最大", "抖动", "评级"]
    lines = []
    for r in rows:
        lines.append([
            str(r["target"]), r["proto"].upper(), f'{r["host"]}',
            str(r["sent"]), f'{r["loss"]:.1f}',
            "-" if r["mn"] is None else f'{r["mn"]:.1f}',
            "-" if r["avg"] is None else f'{r["avg"]:.1f}',
            "-" if r["med"] is None else f'{r["med"]:.1f}',
            "-" if r["p95"] is None else f'{r["p95"]:.1f}',
            "-" if r["mx"] is None else f'{r["mx"]:.1f}',
            "-" if r["jitter"] is None else f'{r["jitter"]:.1f}',
            r["grade"],
        ])
    widths = [max(len(str(x)) for x in col) for col in zip(head, *lines)]
    print("  ".join(str(h).ljust(w) for h, w in zip(head, widths)))
    print("  ".join("-" * w for w in widths))
    for ln in lines:
        print("  ".join(str(c).rjust(w) for c, w in zip(ln, widths)))


def _cmd_analyze(args: argparse.Namespace) -> int:
    """Cross-session trend table (no web server needed)."""
    from .db import Database

    db = Database(args.db)
    tgt = args.target_id
    rows = db.daily_trend(days=args.days, target_id=tgt)
    if args.by_target:
        print(f"近 {args.days} 天每日汇总\n")
        head = ["日期", "目标", "次数", "丢包%", "平均", "最小", "最大"]
        lines = [[r["day"], r["target"], str(r["sent"]), f'{r["loss"]:.1f}',
                  "-" if r["avg"] is None else f'{r["avg"]:.1f}',
                  "-" if r["mn"] is None else f'{r["mn"]:.1f}',
                  "-" if r["mx"] is None else f'{r["mx"]:.1f}'] for r in rows]
    else:
        grouped: dict[str, list] = {}
        for r in rows:
            grouped.setdefault(r["target"], []).append(r)
        print(f"近 {args.days} 天趋势（首 1/3 对比末 1/3）\n")
        head = ["目标", "天", "总平均", "变化", "方向"]
        lines = []
        for name, items in sorted(grouped.items()):
            vals = [i["avg"] for i in items if i["avg"] is not None]
            if not vals:
                lines.append([name, str(len(items)), "-", "-", "-"])
                continue
            k = max(1, len(vals) // 3)
            a = sum(vals[:k]) / k
            b = sum(vals[-k:]) / k
            delta = b - a
            pct = delta * 100.0 / a if a else 0.0
            direction = "→ 平稳" if abs(pct) < 10 else ("↗ 变慢" if pct > 0 else "↘ 变快")
            lines.append([name, str(len(items)), f"{sum(vals) / len(vals):.1f}",
                          f"{delta:+.1f}", direction])
    if not lines:
        print("没有数据（先跑一次 test 或 serve 里测一轮）")
        return 0
    widths = [max(len(str(x)) for x in col) for col in zip(head, *lines)]
    print("  ".join(str(h).ljust(w) for h, w in zip(head, widths)))
    print("  ".join("-" * w for w in widths))
    for ln in lines:
        print("  ".join(str(c).rjust(w) for c, w in zip(ln, widths)))
    return 0


def _cmd_report(args: argparse.Namespace) -> int:
    """Write a self-contained HTML report for one session (or the latest)."""
    from .db import Database
    from .report import build_report

    db = Database(args.db)
    sid = args.session
    if sid is None:
        sessions = db.list_sessions(limit=1)
        if not sessions:
            print("还没有任何会话，先跑一次测试", file=sys.stderr)
            return 2
        sid = int(sessions[0]["id"])
    try:
        page = build_report(db, sid)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else Path(f"report-session-{sid}.html")
    out.write_text(page, encoding="utf-8")
    print(f"报告已生成：{out.resolve()}（{len(page)} 字节，单文件可直接分享）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="gameping", description="游戏平台网络质量测试")
    ap.add_argument("--db", default=None, help=f"SQLite 路径（默认 {DB_PATH}）")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("serve", help="启动 Web 界面（默认）")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8848)
    sp.set_defaults(func=_cmd_serve)

    st = sub.add_parser("test", help="命令行跑一轮测试并入库")
    st.add_argument("-t", "--duration", type=int, default=60, help="时长秒，默认 60")
    st.add_argument("-i", "--interval", type=float, default=5.0, help="间隔秒，默认 5")
    st.add_argument("--timeout", type=float, default=1.0)
    st.add_argument("-n", "--rounds", type=int, default=0, help="只测 N 轮")
    st.add_argument("--targets", default=None, help="逗号分隔：名称/主机/id")
    st.set_defaults(func=_cmd_test)

    sa = sub.add_parser("analyze", help="跨会话趋势分析（不开网页）")
    sa.add_argument("--days", type=int, default=30, help="回看天数，默认 30")
    sa.add_argument("--target-id", type=int, default=None, help="只看某目标")
    sa.add_argument("--by-target", action="store_true", help="按天×目标列明细")
    sa.set_defaults(func=_cmd_analyze)

    sr = sub.add_parser("report", help="导出单文件 HTML 报告")
    sr.add_argument("-s", "--session", type=int, default=None, help="会话 ID，默认最近一次")
    sr.add_argument("-o", "--out", default=None, help="输出路径")
    sr.set_defaults(func=_cmd_report)

    args = ap.parse_args(argv)
    if not getattr(args, "func", None):          # 不带子命令 => 默认 serve
        return _cmd_serve(argparse.Namespace(db=args.db, host="127.0.0.1", port=8848))
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
