"""Daily scheduler: runs a stored schedule at its HH:MM every day.

A single daemon thread wakes every `tick` seconds, checks enabled schedules, and
starts a test session through the shared Runner. Missed occurrences are NOT
back-filled (a laptop asleep at 08:30 should not fire six tests at 09:00) — only
the most recent occurrence inside a grace window is honoured.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta
from typing import Callable

from .db import Database, now_iso

GRACE_MINUTES = 30          # 迟到超过这个窗口就不再补跑


def parse_hhmm(text: str) -> tuple[int, int]:
    hh, _, mm = str(text).partition(":")
    h, m = int(hh), int(mm or 0)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"非法时间：{text!r}")
    return h, m


def due_now(at_time: str, now: datetime, grace_minutes: int = GRACE_MINUTES) -> bool:
    """True when `now` falls in [HH:MM, HH:MM+grace) of today."""
    h, m = parse_hhmm(at_time)
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return target <= now < target + timedelta(minutes=grace_minutes)


class Scheduler:
    def __init__(self, db: Database, runner, tick: float = 20.0) -> None:
        self.db = db
        self.runner = runner
        self.tick = tick
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_error = ""
        self.fired: list[dict] = []          # 最近触发记录（内存，便于界面展示）

    # ---------- lifecycle ----------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="gameping-scheduler",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ---------- loop ----------
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.tick_once()
            except Exception as exc:                  # 调度器不能因一次异常停摆
                self.last_error = f"{type(exc).__name__}: {exc}"
            self._stop.wait(self.tick)

    def tick_once(self, now: datetime | None = None) -> list[dict]:
        """检查所有启用的计划，满足条件就启动测试。返回本次触发列表。"""
        now = now or datetime.now()
        fired: list[dict] = []
        today = now.date().isoformat()
        for sch in self.db.list_schedules(enabled_only=True):
            if not due_now(sch["at_time"], now):
                continue
            last = (sch.get("last_run_at") or "")[:10]
            if last == today:
                continue                              # 今天已经跑过
            if self.runner.running:
                continue                              # 正在测别的，跳过（下一 tick 重试）
            ids = [int(x) for x in (sch["target_ids"] or "").split(",") if x.strip()]
            try:
                state = self.runner.start(
                    duration=int(sch["duration"]), interval=float(sch["interval"]),
                    timeout=float(sch["timeout"]), target_ids=ids or None,
                    note=f"定时:{sch['name']}")
            except RuntimeError as exc:
                self.last_error = str(exc)
                continue
            self.db.update_schedule(sch["id"], last_run_at=now_iso(),
                                    last_session_id=state["session_id"])
            rec = {"schedule_id": sch["id"], "name": sch["name"],
                   "session_id": state["session_id"], "at": now_iso()}
            fired.append(rec)
            self.fired = ([rec] + self.fired)[:20]
        return fired


__all__ = ["Scheduler", "due_now", "parse_hhmm", "GRACE_MINUTES"]
