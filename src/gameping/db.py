"""SQLite persistence: targets, sessions and individual samples."""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Iterator, Sequence

from .config import DB_PATH, DEFAULT_TARGETS, ensure_dirs

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS target (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    host        TEXT    NOT NULL,
    port        INTEGER NOT NULL DEFAULT 443,
    proto       TEXT    NOT NULL DEFAULT 'tcp',
    grp         TEXT    NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    note        TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS session (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    duration    INTEGER NOT NULL DEFAULT 0,   -- 计划时长(秒)，0 表示手动停止
    interval    REAL    NOT NULL DEFAULT 5.0,
    timeout     REAL    NOT NULL DEFAULT 1.0,
    rounds      INTEGER NOT NULL DEFAULT 0,
    rounds_done INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL DEFAULT 'running',  -- running|done|stopped|error
    note        TEXT    NOT NULL DEFAULT '',
    target_ids  TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sample (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES session(id) ON DELETE CASCADE,
    target_id  INTEGER NOT NULL REFERENCES target(id) ON DELETE CASCADE,
    ts         TEXT    NOT NULL,
    rnd        INTEGER NOT NULL,
    ok         INTEGER NOT NULL,
    ms         REAL,
    ip         TEXT    NOT NULL DEFAULT '',
    error      TEXT    NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_sample_session ON sample(session_id, target_id, id);
CREATE INDEX IF NOT EXISTS idx_sample_ts      ON sample(ts);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);

-- 定时任务：每天某个时刻自动跑一次测试
CREATE TABLE IF NOT EXISTS schedule (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    at_time     TEXT    NOT NULL,              -- HH:MM
    duration    INTEGER NOT NULL DEFAULT 300,
    interval    REAL    NOT NULL DEFAULT 5.0,
    timeout     REAL    NOT NULL DEFAULT 1.0,
    target_ids  TEXT    NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_session_id INTEGER,
    created_at  TEXT    NOT NULL,
    UNIQUE(name, at_time)                      -- 同名同时刻视为同一个计划
);

-- 告警：会话结束时按阈值判定，超标就落一条
CREATE TABLE IF NOT EXISTS alert (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT    NOT NULL,
    target_id  INTEGER,
    target     TEXT    NOT NULL,
    kind       TEXT    NOT NULL,               -- latency|loss|jitter
    value      REAL    NOT NULL,
    threshold  REAL    NOT NULL,
    session_id INTEGER,
    message    TEXT    NOT NULL DEFAULT '',
    seen       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alert_seen ON alert(seen, id);
"""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Database:
    """Thread-safe SQLite wrapper. FastAPI handlers and the runner share one instance."""

    def __init__(self, path: str | "os.PathLike[str]" | None = None) -> None:
        ensure_dirs()
        self.path = str(path or DB_PATH)
        self._lock = threading.RLock()
        self._local = threading.local()
        with self.conn() as c:
            c.executescript(_SCHEMA)
            c.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema', ?)",
                      (str(SCHEMA_VERSION),))
        self._migrate()
        self.seed_targets()

    def _migrate(self) -> None:
        """Idempotent upgrades for databases created by earlier versions."""
        with self.tx() as c:
            # 1.1: schedule 加 (name, at_time) 唯一性——先清掉历史重复行
            dupes = c.execute(
                "SELECT name, at_time, MIN(id) AS keep FROM schedule"
                " GROUP BY name, at_time HAVING COUNT(*) > 1").fetchall()
            for row in dupes:
                c.execute("DELETE FROM schedule WHERE name=? AND at_time=? AND id<>?",
                          (row["name"], row["at_time"], row["keep"]))
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_schedule_name_time"
                      " ON schedule(name, at_time)")
            # 1.1: alert 表新增前的库没有该表，executescript 已建好，这里只保证索引存在
            c.execute("CREATE INDEX IF NOT EXISTS idx_alert_seen ON alert(seen, id)")
            c.execute("UPDATE meta SET value=? WHERE key='schema'", (str(SCHEMA_VERSION),))

    # ---------- connection plumbing ----------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0, isolation_level=None,
                               check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @property
    def _conn(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = self._connect()
            self._local.conn = conn
        return conn

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = self._conn
            try:
                yield conn
            finally:
                pass

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """Explicit transaction; rolls back on exception."""
        with self._lock:
            conn = self._conn
            conn.execute("BEGIN")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # ---------- targets ----------
    def seed_targets(self) -> int:
        with self.conn() as c:
            count = c.execute("SELECT COUNT(*) FROM target").fetchone()[0]
        if count:
            return 0
        with self.tx() as c:
            for t in DEFAULT_TARGETS:
                c.execute(
                    "INSERT OR IGNORE INTO target(name, host, port, proto, grp, created_at)"
                    " VALUES(?,?,?,?,?,?)",
                    (t["name"], t["host"], t["port"], t["proto"], t.get("group", ""),
                     now_iso()),
                )
        return len(DEFAULT_TARGETS)

    def list_targets(self, enabled_only: bool = True) -> list[dict[str, Any]]:
        sql = "SELECT * FROM target"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY grp, id"
        with self.conn() as c:
            rows = c.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_target(self, target_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM target WHERE id=?", (target_id,)).fetchone()
        return dict(row) if row else None

    def add_target(self, name: str, host: str, port: int = 443, proto: str = "tcp",
                   group: str = "", note: str = "") -> dict[str, Any]:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO target(name, host, port, proto, grp, note, created_at)"
                " VALUES(?,?,?,?,?,?,?)",
                (name, host, int(port), proto, group, note, now_iso()),
            )
            tid = cur.lastrowid
        got = self.get_target(int(tid))
        assert got is not None
        return got

    def update_target(self, target_id: int, **fields: Any) -> dict[str, Any] | None:
        allowed = {"name", "host", "port", "proto", "grp", "enabled", "note"}
        patch = {k: v for k, v in fields.items() if k in allowed}
        if not patch:
            return self.get_target(target_id)
        sets = ", ".join(f"{k}=?" for k in patch)
        with self.tx() as c:
            c.execute(f"UPDATE target SET {sets} WHERE id=?", (*patch.values(), target_id))
        return self.get_target(target_id)

    def delete_target(self, target_id: int) -> bool:
        with self.tx() as c:
            cur = c.execute("DELETE FROM target WHERE id=?", (target_id,))
            return cur.rowcount > 0

    # ---------- sessions ----------
    def create_session(self, duration: int, interval: float, timeout: float,
                       target_ids: Sequence[int], rounds: int = 0,
                       note: str = "") -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO session(started_at, duration, interval, timeout, rounds, note,"
                " target_ids, status) VALUES(?,?,?,?,?,?,?,'running')",
                (now_iso(), int(duration), float(interval), float(timeout), int(rounds),
                 note, ",".join(str(i) for i in target_ids)),
            )
            return int(cur.lastrowid)

    def update_session(self, session_id: int, **fields: Any) -> None:
        allowed = {"finished_at", "rounds_done", "status", "note"}
        patch = {k: v for k, v in fields.items() if k in allowed}
        if not patch:
            return
        sets = ", ".join(f"{k}=?" for k in patch)
        with self.tx() as c:
            c.execute(f"UPDATE session SET {sets} WHERE id=?", (*patch.values(), session_id))

    def get_session(self, session_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM session WHERE id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self.conn() as c:
            rows = c.execute(
                "SELECT * FROM session ORDER BY id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_sessions(self) -> int:
        with self.conn() as c:
            return int(c.execute("SELECT COUNT(*) FROM session").fetchone()[0])

    def delete_session(self, session_id: int) -> bool:
        with self.tx() as c:
            c.execute("DELETE FROM sample WHERE session_id=?", (session_id,))
            cur = c.execute("DELETE FROM session WHERE id=?", (session_id,))
            return cur.rowcount > 0

    # ---------- samples ----------
    def add_samples(self, session_id: int, rnd: int, rows: Iterable[dict[str, Any]]) -> int:
        payload = [
            (session_id, r["target_id"], r["ts"], rnd, 1 if r["ok"] else 0,
             r.get("ms"), r.get("ip", ""), r.get("error", ""))
            for r in rows
        ]
        if not payload:
            return 0
        with self.tx() as c:
            c.executemany(
                "INSERT INTO sample(session_id, target_id, ts, rnd, ok, ms, ip, error)"
                " VALUES(?,?,?,?,?,?,?,?)", payload)
        return len(payload)

    def fetch_samples(self, session_id: int, target_id: int | None = None,
                      since_round: int = 0) -> list[dict[str, Any]]:
        sql = ("SELECT s.ts, s.rnd, s.ok, s.ms, s.ip, s.error, t.name AS target,"
               " t.host, t.proto, s.target_id"
               " FROM sample s JOIN target t ON t.id = s.target_id"
               " WHERE s.session_id=? AND s.rnd>=?")
        params: list[Any] = [session_id, since_round]
        if target_id is not None:
            sql += " AND s.target_id=?"
            params.append(target_id)
        sql += " ORDER BY s.id"
        with self.conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def session_summary(self, session_id: int) -> list[dict[str, Any]]:
        """以 SQL 聚合出每个目标的丢包/均值等（长会话不必把所有样本读进内存）。"""
        sql = """
        SELECT t.id AS target_id, t.name AS target, t.host, t.proto, t.grp,
               COUNT(s.id)              AS sent,
               SUM(s.ok)                AS recv,
               AVG(CASE WHEN s.ok=1 THEN s.ms END) AS avg,
               MIN(CASE WHEN s.ok=1 THEN s.ms END) AS mn,
               MAX(CASE WHEN s.ok=1 THEN s.ms END) AS mx
        FROM sample s JOIN target t ON t.id = s.target_id
        WHERE s.session_id = ?
        GROUP BY t.id
        ORDER BY t.grp, t.id
        """
        with self.conn() as c:
            rows = c.execute(sql, (session_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            sent = int(d["sent"] or 0)
            recv = int(d["recv"] or 0)
            d["loss"] = round((sent - recv) * 100.0 / sent, 1) if sent else 0.0
            d["ok_rate"] = round(recv * 100.0 / sent, 1) if sent else 0.0
            for key in ("avg", "mn", "mx"):
                d[key] = round(d[key], 1) if d[key] is not None else None
            out.append(d)
        return out

    def purge_older_than(self, days: int) -> int:
        """Retention: drop sessions (and their samples) older than `days`."""
        if days <= 0:
            return 0
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        with self.conn() as c:
            ids = [r[0] for r in c.execute(
                "SELECT id FROM session WHERE started_at < ?", (cutoff,)).fetchall()]
        for sid in ids:
            self.delete_session(int(sid))
        return len(ids)

    def stats_overview(self) -> dict[str, Any]:
        with self.conn() as c:
            sessions = c.execute("SELECT COUNT(*) FROM session").fetchone()[0]
            samples = c.execute("SELECT COUNT(*) FROM sample").fetchone()[0]
            targets = c.execute("SELECT COUNT(*) FROM target WHERE enabled=1").fetchone()[0]
            first = c.execute("SELECT MIN(started_at) FROM session").fetchone()[0]
            last = c.execute("SELECT MAX(ts) FROM sample").fetchone()[0]
            alerts = c.execute("SELECT COUNT(*) FROM alert WHERE seen=0").fetchone()[0]
        try:
            size = __import__("os").path.getsize(self.path)
        except OSError:
            size = 0
        return {"sessions": sessions, "samples": samples, "targets": targets,
                "first_session": first, "last_sample": last, "db_bytes": size,
                "unseen_alerts": alerts}

    # ---------- cross-session analytics ----------
    def daily_trend(self, days: int = 30, target_id: int | None = None,
                    note: str | None = None) -> list[dict[str, Any]]:
        """按天聚合：每天每目标的平均/丢包，用于「这周是不是变差了」。

        `note` 可按会话备注过滤（例如只看「手机热点」的那些会话）。
        """
        cutoff = (datetime.now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
        sql = """
        SELECT substr(s.ts, 1, 10) AS day,
               t.id AS target_id, t.name AS target, t.grp,
               COUNT(s.id) AS sent,
               SUM(s.ok)   AS recv,
               AVG(CASE WHEN s.ok=1 THEN s.ms END) AS avg,
               MIN(CASE WHEN s.ok=1 THEN s.ms END) AS mn,
               MAX(CASE WHEN s.ok=1 THEN s.ms END) AS mx
        FROM sample s
        JOIN target t  ON t.id = s.target_id
        JOIN session se ON se.id = s.session_id
        WHERE s.ts >= ?
        """
        params: list[Any] = [cutoff]
        if target_id is not None:
            sql += " AND t.id = ?"
            params.append(target_id)
        if note:
            sql += " AND se.note = ?"
            params.append(note)
        sql += " GROUP BY day, t.id ORDER BY day, t.grp, t.id"
        with self.conn() as c:
            rows = c.execute(sql, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            sent, recv = int(d["sent"] or 0), int(d["recv"] or 0)
            d["loss"] = round((sent - recv) * 100.0 / sent, 1) if sent else 0.0
            for k in ("avg", "mn", "mx"):
                d[k] = round(d[k], 1) if d[k] is not None else None
            out.append(d)
        return out

    def list_notes(self) -> list[dict[str, Any]]:
        """所有出现过的会话备注及其实测样本数，用于「链路对比」筛选器。"""
        sql = """
        SELECT se.note AS note,
               COUNT(DISTINCT se.id) AS sessions,
               COUNT(s.id) AS samples,
               MIN(se.started_at) AS first_at,
               MAX(se.started_at) AS last_at
        FROM session se LEFT JOIN sample s ON s.session_id = se.id
        WHERE se.note <> ''
        GROUP BY se.note
        ORDER BY MAX(se.started_at) DESC
        """
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql).fetchall()]

    def note_summary(self, note: str) -> list[dict[str, Any]]:
        """某条备注（一条链路）下每个目标的总体表现，用于跨链路对比。"""
        sql = """
        SELECT t.id AS target_id, t.name AS target, t.grp,
               COUNT(s.id) AS sent, SUM(s.ok) AS recv,
               AVG(CASE WHEN s.ok=1 THEN s.ms END) AS avg,
               MIN(CASE WHEN s.ok=1 THEN s.ms END) AS mn,
               MAX(CASE WHEN s.ok=1 THEN s.ms END) AS mx
        FROM sample s
        JOIN target t   ON t.id = s.target_id
        JOIN session se ON se.id = s.session_id
        WHERE se.note = ?
        GROUP BY t.id
        ORDER BY t.grp, t.id
        """
        with self.conn() as c:
            rows = c.execute(sql, (note,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            sent, recv = int(d["sent"] or 0), int(d["recv"] or 0)
            d["loss"] = round((sent - recv) * 100.0 / sent, 1) if sent else 0.0
            for k in ("avg", "mn", "mx"):
                d[k] = round(d[k], 1) if d[k] is not None else None
            out.append(d)
        return out

    def heatmap(self, days: int = 14, bucket: str = "hour") -> list[dict[str, Any]]:
        """丢包/延迟热力图：按 (目标 × 时段) 聚合。

        bucket='hour' → 0..23；bucket='dow' → 0..6（周一到周日）。
        """
        cutoff = (datetime.now() - timedelta(days=max(1, days))).isoformat(timespec="seconds")
        expr = "CAST(substr(s.ts,12,2) AS INTEGER)" if bucket == "hour" else \
               "CAST(strftime('%w', s.ts) AS INTEGER)"
        sql = f"""
        SELECT t.id AS target_id, t.name AS target, {expr} AS slot,
               COUNT(s.id) AS sent, SUM(s.ok) AS recv,
               AVG(CASE WHEN s.ok=1 THEN s.ms END) AS avg
        FROM sample s JOIN target t ON t.id=s.target_id
        WHERE s.ts >= ?
        GROUP BY t.id, slot
        ORDER BY t.grp, t.id, slot
        """
        with self.conn() as c:
            rows = c.execute(sql, (cutoff,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            sent, recv = int(d["sent"] or 0), int(d["recv"] or 0)
            d["loss"] = round((sent - recv) * 100.0 / sent, 1) if sent else 0.0
            d["avg"] = round(d["avg"], 1) if d["avg"] is not None else None
            out.append(d)
        return out

    def samples_between(self, start: str, end: str,
                        target_ids: Sequence[int] | None = None) -> list[dict[str, Any]]:
        """任意时间窗内的样本（跨会话），供叠加曲线与报表使用。"""
        sql = ("SELECT s.ts, s.ok, s.ms, s.target_id, t.name AS target, t.host, t.proto"
               " FROM sample s JOIN target t ON t.id=s.target_id"
               " WHERE s.ts >= ? AND s.ts <= ?")
        params: list[Any] = [start, end]
        if target_ids:
            marks = ",".join("?" for _ in target_ids)
            sql += f" AND t.id IN ({marks})"
            params.extend(int(i) for i in target_ids)
        sql += " ORDER BY s.ts"
        with self.conn() as c:
            rows = c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # ---------- schedules ----------
    _SCHED_FIELDS = ("name", "at_time", "duration", "interval", "timeout",
                     "target_ids", "enabled", "last_run_at", "last_session_id")

    def list_schedules(self, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM schedule"
        if enabled_only:
            sql += " WHERE enabled = 1"
        sql += " ORDER BY at_time"
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql).fetchall()]

    def get_schedule(self, schedule_id: int) -> dict[str, Any] | None:
        with self.conn() as c:
            row = c.execute("SELECT * FROM schedule WHERE id=?", (schedule_id,)).fetchone()
        return dict(row) if row else None

    def add_schedule(self, name: str, at_time: str, duration: int = 300,
                     interval: float = 5.0, timeout: float = 1.0,
                     target_ids: Sequence[int] = ()) -> dict[str, Any]:
        """Create a plan. Re-adding the same (name, at_time) updates it instead of duplicating."""
        ids = ",".join(str(i) for i in target_ids)
        with self.tx() as c:
            row = c.execute("SELECT id FROM schedule WHERE name=? AND at_time=?",
                            (name, at_time)).fetchone()
            if row:
                c.execute(
                    "UPDATE schedule SET duration=?, interval=?, timeout=?, target_ids=?"
                    " WHERE id=?",
                    (int(duration), float(interval), float(timeout), ids, row["id"]))
                sid = int(row["id"])
            else:
                cur = c.execute(
                    "INSERT INTO schedule(name, at_time, duration, interval, timeout,"
                    " target_ids, created_at) VALUES(?,?,?,?,?,?,?)",
                    (name, at_time, int(duration), float(interval), float(timeout),
                     ids, now_iso()))
                sid = int(cur.lastrowid)
        got = self.get_schedule(sid)
        assert got is not None
        return got

    def update_schedule(self, schedule_id: int, **fields: Any) -> dict[str, Any] | None:
        patch = {k: v for k, v in fields.items() if k in self._SCHED_FIELDS}
        if not patch:
            return self.get_schedule(schedule_id)
        sets = ", ".join(f"{k}=?" for k in patch)
        with self.tx() as c:
            c.execute(f"UPDATE schedule SET {sets} WHERE id=?",
                      (*patch.values(), schedule_id))
        return self.get_schedule(schedule_id)

    def delete_schedule(self, schedule_id: int) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM schedule WHERE id=?", (schedule_id,)).rowcount > 0

    # ---------- alerts ----------
    def add_alert(self, target: str, kind: str, value: float, threshold: float,
                  *, target_id: int | None = None, session_id: int | None = None,
                  message: str = "") -> int:
        with self.tx() as c:
            cur = c.execute(
                "INSERT INTO alert(created_at, target_id, target, kind, value,"
                " threshold, session_id, message) VALUES(?,?,?,?,?,?,?,?)",
                (now_iso(), target_id, target, kind, float(value),
                 float(threshold), session_id, message))
            return int(cur.lastrowid)

    def list_alerts(self, limit: int = 50, unseen_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM alert"
        if unseen_only:
            sql += " WHERE seen = 0"
        sql += " ORDER BY id DESC LIMIT ?"
        with self.conn() as c:
            return [dict(r) for r in c.execute(sql, (limit,)).fetchall()]

    def mark_alerts_seen(self, alert_id: int | None = None) -> int:
        with self.tx() as c:
            if alert_id is None:
                cur = c.execute("UPDATE alert SET seen=1 WHERE seen=0")
            else:
                cur = c.execute("UPDATE alert SET seen=1 WHERE id=?", (alert_id,))
            return cur.rowcount

    # ---------- import ----------
    def import_samples(self, rows: Iterable[dict[str, Any]], *, note: str = "",
                       target_map: dict[str, int] | None = None) -> dict[str, Any]:
        """把外部 CSV/JSON 样本导入成一个新会话。

        rows: {ts, target|target_id, ok, ms, ip?}。未匹配到的目标会被自动创建
        （host 取 target 字段本身，端口 443），保证导入不丢数据。
        """
        rows = list(rows)
        if not rows:
            return {"session_id": None, "imported": 0, "created_targets": []}
        created: list[str] = []
        resolve_cache: dict[str, int | None] = dict(target_map or {})
        by_name = {t["name"]: t["id"] for t in self.list_targets(enabled_only=False)}

        def target_of(row: dict[str, Any]) -> int:
            if row.get("target_id"):
                return int(row["target_id"])
            name = str(row.get("target") or row.get("host") or "").strip()
            if not name:
                raise ValueError("导入行缺少 target 字段")
            if name in by_name:
                return by_name[name]
            tid = resolve_cache.get(name)
            if tid is None:
                try:
                    tid = self.add_target(name, name, 443, "tcp", "导入")["id"]
                    created.append(name)
                except Exception:
                    tid = {t["name"]: t["id"] for t in
                           self.list_targets(enabled_only=False)}[name]
                resolve_cache[name] = tid
                by_name[name] = tid
            return int(tid)

        prepared = []
        for row in rows:
            try:
                ok = bool(int(row.get("ok", 1))) if str(row.get("ok")).isdigit() \
                    else bool(row.get("ok", True))
            except (TypeError, ValueError):
                ok = True
            ms = row.get("ms")
            prepared.append({
                "target_id": target_of(row),
                "ts": str(row.get("ts") or now_iso()),
                "ok": ok,
                "ms": None if ms in (None, "", "-") else float(ms),
                "ip": str(row.get("ip") or ""),
            })

        stamps = sorted(p["ts"] for p in prepared)
        ts_min, ts_max = stamps[0], stamps[-1]
        sid = self.create_session(duration=0, interval=0, timeout=0,
                                  target_ids=sorted({p["target_id"] for p in prepared}),
                                  rounds=0, note=note)
        # 按目标重新编号轮次，保证时间序
        rounds: dict[int, int] = {}
        for p in prepared:
            rounds[p["target_id"]] = rounds.get(p["target_id"], 0) + 1
            p["rnd"] = rounds[p["target_id"]]
        with self.tx() as c:
            c.executemany(
                "INSERT INTO sample(session_id, target_id, ts, rnd, ok, ms, ip, error)"
                " VALUES(?,?,?,?,?,?,?,'')",
                [(sid, p["target_id"], p["ts"], p["rnd"], 1 if p["ok"] else 0,
                  p["ms"], p["ip"]) for p in prepared])
            c.execute("UPDATE session SET started_at=?, finished_at=?, status='done',"
                      " rounds_done=? WHERE id=?",
                      (ts_min, ts_max, max(rounds.values()), sid))
        return {"session_id": sid, "imported": len(prepared),
                "created_targets": created, "from": ts_min, "to": ts_max}

