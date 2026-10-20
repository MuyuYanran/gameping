"""Background test runner: one live session at a time, thread-based."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

from .db import Database, now_iso
from .probe import ProbeTarget
from .stats import summarize

MAX_ROUNDS_SAFETY = 1_000_000


@dataclass
class LiveState:
    """Mutable snapshot of the running session, polled by the web UI."""

    session_id: int = 0
    running: bool = False
    started_at: str = ""
    plan_seconds: int = 0
    rounds_done: int = 0
    rounds_total: int = 0
    interval: float = 5.0
    timeout: float = 1.0
    elapsed: float = 0.0
    remaining: float | None = None
    last_round_at: str = ""
    error: str = ""
    targets: list[dict] = field(default_factory=list)

    def snapshot(self) -> dict:
        return {
            "session_id": self.session_id,
            "running": self.running,
            "started_at": self.started_at,
            "plan_seconds": self.plan_seconds,
            "rounds_done": self.rounds_done,
            "rounds_total": self.rounds_total,
            "interval": self.interval,
            "timeout": self.timeout,
            "elapsed": round(self.elapsed, 1),
            "remaining": None if self.remaining is None else round(self.remaining, 1),
            "last_round_at": self.last_round_at,
            "error": self.error,
            "targets": [dict(t) for t in self.targets],
        }


class Runner:
    def __init__(self, db: Database) -> None:
        self.db = db
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.state = LiveState()
        self._lock = threading.RLock()
        self._watchers: list[Callable[[int, int], None]] = []
        # 增量聚合：每轮把结果累加进内存，live 查询不再全表扫描。
        self._acc: dict[int, dict] = {}
        self.alerts: list[dict] = []          # 最近一次会话触发的告警
        self.alerts_error = ""

    # ---------- lifecycle ----------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self.state.running)

    def start(self, *, duration: int = 300, interval: float = 5.0, timeout: float = 1.0,
              target_ids: list[int] | None = None, rounds: int = 0,
              note: str = "") -> dict:
        with self._lock:
            if self.running:
                raise RuntimeError("已有测试在运行，请先停止")

            all_targets = self.db.list_targets()
            if target_ids:
                wanted = set(target_ids)
                chosen = [t for t in all_targets if t["id"] in wanted]
            else:
                chosen = all_targets
            if not chosen:
                raise RuntimeError("没有可用的测试目标")

            targets = [
                ProbeTarget(id=t["id"], name=t["name"], host=t["host"],
                            port=int(t["port"]), proto=t["proto"],
                            group=t.get("grp", "") or "")
                for t in chosen
            ]
            rounds = max(0, int(rounds))
            duration = max(0, int(duration))
            if rounds == 0 and duration == 0:
                raise RuntimeError("时长与轮数不能同时为 0")

            session_id = self.db.create_session(
                duration=duration, interval=interval, timeout=timeout,
                target_ids=[t.id for t in targets], rounds=rounds, note=note,
            )
            planned = rounds if rounds else (int(duration / max(interval, 0.05)) if interval > 0
                                             else duration)
            self._stop.clear()
            self._acc = {}
            self.state = LiveState(
                session_id=session_id, running=True, started_at=now_iso(),
                plan_seconds=duration, rounds_done=0,
                rounds_total=min(planned, MAX_ROUNDS_SAFETY),
                interval=interval, timeout=timeout,
                targets=[{"id": t.id, "name": t.name, "host": t.host,
                          "port": t.port, "proto": t.proto, "group": t.group}
                         for t in targets],
            )
            self._thread = threading.Thread(
                target=self._loop, args=(session_id, targets, duration, interval, timeout, rounds),
                name=f"gameping-runner-{session_id}", daemon=True,
            )
            self._thread.start()
            return self.state.snapshot()

    def stop(self) -> dict:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=3.0)
        return self.state.snapshot()

    def wait(self, timeout: float | None = None) -> None:
        if self._thread:
            self._thread.join(timeout=timeout)

    def hydrate(self, session_id: int) -> bool:
        """Reattach to a finished session after a restart (state + aggregate)."""
        sess = self.db.get_session(session_id)
        if not sess or sess["status"] == "running":
            return False
        ids = [int(x) for x in (sess["target_ids"] or "").split(",") if x.strip()]
        targets = [t for t in self.db.list_targets(enabled_only=False) if t["id"] in ids]
        rows = self.db.fetch_samples(session_id)
        with self._lock:
            self._acc = {}
            self.state = LiveState(
                session_id=session_id, running=False, started_at=sess["started_at"],
                plan_seconds=int(sess["duration"] or 0),
                rounds_done=int(sess["rounds_done"] or 0),
                rounds_total=int(sess["rounds"] or sess["rounds_done"] or 0),
                interval=float(sess["interval"] or 0),
                timeout=float(sess["timeout"] or 0),
                targets=[{"id": t["id"], "name": t["name"], "host": t["host"],
                          "port": t["port"], "proto": t["proto"],
                          "group": t.get("grp", "")} for t in targets],
            )
        for r in rows:
            self._accumulate([{"target_id": r["target_id"], "ok": bool(r["ok"]),
                               "ms": r["ms"]}])
        return True

    # ---------- core loop ----------
    def _loop(self, session_id: int, targets: list[ProbeTarget], duration: int,
              interval: float, timeout: float, rounds: int) -> None:
        t_begin = time.monotonic()
        deadline = t_begin + duration if duration else None
        max_rounds = rounds if rounds else MAX_ROUNDS_SAFETY
        rnd = 0
        status = "done"
        pool = ThreadPoolExecutor(max_workers=max(4, min(32, len(targets))),
                                  thread_name_prefix="gameping-probe")
        try:
            for t in targets:
                t.refresh_ip()
            while rnd < max_rounds:
                if self._stop.is_set():
                    status = "stopped"
                    break
                if deadline is not None and time.monotonic() >= deadline:
                    break
                rnd += 1
                batch = self._round(session_id, rnd, targets, timeout, pool)
                self.db.add_samples(session_id, rnd, batch)
                self._accumulate(batch)
                elapsed = time.monotonic() - t_begin
                with self._lock:
                    self.state.rounds_done = rnd
                    self.state.elapsed = elapsed
                    self.state.last_round_at = now_iso()
                    self.state.remaining = (max(0.0, deadline - time.monotonic())
                                            if deadline else None)
                for watcher in list(self._watchers):
                    try:
                        watcher(session_id, rnd)
                    except Exception:
                        pass
                if rnd >= max_rounds:
                    break
                if self._sleep_until(deadline, interval):
                    status = "stopped"
                    break
        except Exception as exc:                       # keep the session record honest
            status = "error"
            with self._lock:
                self.state.error = str(exc)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
            self.db.update_session(session_id, finished_at=now_iso(),
                                   rounds_done=self.state.rounds_done, status=status)
            with self._lock:
                self.state.running = False
                self.state.elapsed = time.monotonic() - t_begin
                self.state.remaining = 0.0
            # 会话收尾后按阈值判定告警（失败不影响会话状态）
            try:
                from .alerts import check_session
                from .config import load_config
                th = load_config().get("thresholds") or {}
                if self.state.rounds_done:
                    self.alerts = check_session(self.db, session_id, th)
            except Exception as exc:
                self.alerts = []
                self.alerts_error = f"{type(exc).__name__}: {exc}"

    def _round(self, session_id: int, rnd: int, targets: list[ProbeTarget],
               timeout: float, pool: ThreadPoolExecutor) -> list[dict]:
        ts = now_iso()
        futures = [(pool.submit(t.probe, timeout), t) for t in targets]
        out: list[dict] = []
        for fut, t in futures:
            ok, ms = False, None
            err = ""
            try:
                ok, ms = fut.result(timeout=timeout + 10.0)
            except Exception as exc:
                err = type(exc).__name__
            out.append({"target_id": t.id, "ts": ts, "ok": ok,
                        "ms": None if ms is None else round(ms, 1),
                        "ip": t.ip, "error": err,
                        # dot 记录本次实际用的地址族，便于事后判断是否走了 IPv6
                        "dot": "v6" if ":" in (t.ip or "") else "v4"})
        return out

    def _sleep_until(self, deadline: float | None, interval: float) -> bool:
        """Sleep `interval` seconds; return True if a stop was requested."""
        end = time.monotonic() + max(0.0, interval)
        while True:
            if self._stop.is_set():
                return True
            now = time.monotonic()
            if now >= end:
                return False
            if deadline is not None and now >= deadline:
                return False
            time.sleep(min(0.1, end - now,
                           (deadline - now) if deadline else 0.1))

    # ---------- analysis helpers used by the API ----------
    def _accumulate(self, batch: list[dict]) -> None:
        """Fold one round into the in-memory aggregate used by /session/live."""
        with self._lock:
            for row in batch:
                acc = self._acc.setdefault(
                    row["target_id"],
                    {"sent": 0, "vals": [], "spark": [], "spark_ts": [], "seq": []})
                acc["sent"] += 1
                # seq 保留完整时间序（含超时），卡顿评分与丢包突发要它
                acc.setdefault("seq", []).append(
                    (row.get("ts", ""), None if not row["ok"] else row["ms"]))
                if row["ok"] and row["ms"] is not None:
                    acc["vals"].append(row["ms"])
                    acc["spark"].append(row["ms"])
                    # 同时留存时间戳，悬停提示才能说清「哪一刻的读数」
                    acc.setdefault("spark_ts", []).append(row.get("ts", ""))
                    if len(acc["spark"]) > 60:
                        del acc["spark"][:-60]
                        del acc["spark_ts"][:-60]

    def live_stats(self) -> list[dict]:
        """Per-target live stats from the in-memory aggregate (no DB scan)."""
        if not self.state.session_id:
            return []
        from .stats import loss_bursts, stutter_score
        from datetime import datetime as _dt
        out: list[dict] = []
        with self._lock:
            for t in self.state.targets:
                acc = self._acc.get(t["id"], {"sent": 0, "vals": [], "spark": []})
                st = summarize(acc["vals"], acc["sent"], target_id=t["id"],
                               target=t["name"], host=t["host"], proto=t["proto"])
                d = st.as_dict()
                d["spark"] = [round(v, 1) for v in acc["spark"]]
                d["spark_ts"] = list(acc.get("spark_ts") or [])
                d["group"] = t.get("group", "")
                # 实时卡顿评分：用带超时点的完整时间序，体感才准
                seq = acc.get("seq") or []
                pts = []
                for ts, ms in seq:
                    try:
                        pts.append((_dt.fromisoformat(ts), ms))
                    except (TypeError, ValueError):
                        continue
                d["stutter"] = stutter_score(pts) if pts else None
                d["bursts"] = loss_bursts(pts) if pts else []
                d["baseline"] = t.get("baseline")
                out.append(d)
        return out
