"""Tests for the v1.1 features: new probe modes, scheduler, alerts, report,
cross-session analytics and CSV/JSON import."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gameping import alerts as A            # noqa: E402
from gameping import probe as P             # noqa: E402
from gameping.db import Database            # noqa: E402
from gameping.report import build_report    # noqa: E402
from gameping.scheduler import due_now, parse_hhmm  # noqa: E402


@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


# ------------------------- probe modes -------------------------
def test_modes_constant():
    assert set(P.MODES) == {"tcp", "icmp", "dns", "http"}


def test_looks_like_ip():
    assert P.looks_like_ip("127.0.0.1")
    assert P.looks_like_ip("223.5.5.5")
    assert P.looks_like_ip("::1")
    assert not P.looks_like_ip("example.com")


def test_dns_ping_on_literal_ip_is_zero():
    ok, ms = P.dns_ping("223.5.5.5", 1.0)
    assert ok and ms == 0.0


def test_dns_ping_localhost():
    ok, ms = P.dns_ping("localhost", 2.0)
    assert ok and ms is not None and ms >= 0


def test_http_ping_closed_port():
    ok, ms = P.http_ping("127.0.0.1", 1, 0.4)
    assert ok is False


def test_probe_target_dispatches_by_mode():
    t = P.ProbeTarget(1, "x", "127.0.0.1", 1, "http")
    ok, ms = t.probe(0.4)
    assert ok is False                      # port 1 not listening
    t2 = P.ProbeTarget(2, "y", "127.0.0.1", 1, "dns")
    ok2, ms2 = t2.probe(1.0)
    assert ok2 is True                      # literal IP resolves instantly


def test_unknown_mode_falls_back_to_tcp():
    t = P.ProbeTarget(1, "x", "127.0.0.1", 1, "bogus")
    assert t.probe(0.3) == (False, None)


# ------------------------- scheduler -------------------------
def test_parse_hhmm():
    assert parse_hhmm("08:30") == (8, 30)
    assert parse_hhmm("7:5") == (7, 5)
    with pytest.raises(ValueError):
        parse_hhmm("25:00")
    with pytest.raises(ValueError):
        parse_hhmm("08:99")


def test_due_now_window():
    day = datetime(2026, 10, 20)
    assert due_now("08:30", day.replace(hour=8, minute=30))
    assert due_now("08:30", day.replace(hour=8, minute=59))
    assert not due_now("08:30", day.replace(hour=7, minute=59))
    assert not due_now("08:30", day.replace(hour=9, minute=5))   # 超出宽限
    assert not due_now("08:30", day.replace(hour=10, minute=0))


# ------------------------- alerts -------------------------
def test_check_session_emits_latency_and_loss(db):
    r = db.import_samples([
        {"ts": "2026-10-20T10:00:00", "target": "慢站", "ok": 1, "ms": 500.0},
        {"ts": "2026-10-20T10:00:01", "target": "慢站", "ok": 1, "ms": 520.0},
        {"ts": "2026-10-20T10:00:02", "target": "丢站", "ok": 0, "ms": None},
        {"ts": "2026-10-20T10:00:03", "target": "丢站", "ok": 0, "ms": None},
    ])
    created = A.check_session(db, r["session_id"])
    kinds = {c["kind"] for c in created}
    assert "latency" in kinds and "loss" in kinds
    assert len(db.list_alerts()) >= 2


def test_check_session_quiet_when_healthy(db):
    r = db.import_samples([
        {"ts": "2026-10-20T10:00:00", "target": "快站", "ok": 1, "ms": 20.0},
        {"ts": "2026-10-20T10:00:01", "target": "快站", "ok": 1, "ms": 21.0},
        {"ts": "2026-10-20T10:00:02", "target": "快站", "ok": 1, "ms": 22.0},
    ])
    assert A.check_session(db, r["session_id"]) == []


def test_alert_seen_flow(db):
    db.add_alert("5E", "latency", 200, 100, message="x")
    assert db.stats_overview()["unseen_alerts"] == 1
    assert db.mark_alerts_seen() == 1
    assert db.stats_overview()["unseen_alerts"] == 0


# ------------------------- schedules CRUD -------------------------
def test_schedule_crud(db):
    s = db.add_schedule("每早", "08:30", 300, 5.0, 1.0, [1, 2])
    assert s["at_time"] == "08:30" and s["target_ids"] == "1,2"
    assert db.get_schedule(s["id"]) is not None
    assert db.update_schedule(s["id"], enabled=0)["enabled"] == 0
    assert len(db.list_schedules(enabled_only=True)) == 0
    assert db.delete_schedule(s["id"]) is True
    assert db.delete_schedule(s["id"]) is False


def test_schedule_same_name_time_updates_not_duplicates(db):
    a = db.add_schedule("每日", "08:30", 300)
    b = db.add_schedule("每日", "08:30", 600)         # 同名同时刻 => 覆盖
    assert a["id"] == b["id"]
    assert b["duration"] == 600
    assert len(db.list_schedules()) == 1

    c = db.add_schedule("每日", "09:00", 60)          # 时刻不同 => 另一条
    assert c["id"] != a["id"]
    assert len(db.list_schedules()) == 2


def test_migrate_dedupes_legacy_duplicates(tmp_path):
    """旧库（schedule 表没有唯一约束）打开时应自动收敛重复计划。"""
    import sqlite3
    from gameping.db import Database as DB

    path = tmp_path / "legacy.db"
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE schedule (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            at_time TEXT NOT NULL,
            duration INTEGER NOT NULL DEFAULT 300,
            interval REAL NOT NULL DEFAULT 5.0,
            timeout REAL NOT NULL DEFAULT 1.0,
            target_ids TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT,
            last_session_id INTEGER,
            created_at TEXT NOT NULL
        );
        INSERT INTO schedule(name, at_time, duration, created_at)
            VALUES('旧计划','07:00',300,'2026-01-01T00:00:00');
        INSERT INTO schedule(name, at_time, duration, created_at)
            VALUES('旧计划','07:00',600,'2026-01-02T00:00:00');
        INSERT INTO schedule(name, at_time, duration, created_at)
            VALUES('旧计划','07:00',900,'2026-01-03T00:00:00');
    """)
    raw.commit()
    raw.close()

    d = DB(path)                                       # 重新打开触发 _migrate
    rows = d.list_schedules()
    assert len(rows) == 1 and rows[0]["name"] == "旧计划"
    assert rows[0]["duration"] == 300                  # 保留最早那条
    d.close()


# ------------------------- analytics -------------------------
def test_daily_trend(db):
    db.import_samples([
        {"ts": "2026-10-19T09:00:00", "target": "A", "ok": 1, "ms": 40.0},
        {"ts": "2026-10-20T09:00:00", "target": "A", "ok": 1, "ms": 80.0},
        {"ts": "2026-10-20T09:00:01", "target": "A", "ok": 0, "ms": None},
    ])
    rows = db.daily_trend(days=365)
    days = {r["day"] for r in rows}
    assert "2026-10-19" in days and "2026-10-20" in days
    latest = [r for r in rows if r["day"] == "2026-10-20"][0]
    assert latest["loss"] == 50.0


def test_heatmap_hour_and_dow(db):
    db.import_samples([
        {"ts": "2026-10-20T14:00:00", "target": "A", "ok": 1, "ms": 30.0},
        {"ts": "2026-10-20T14:00:05", "target": "A", "ok": 1, "ms": 32.0},
    ])
    hours = db.heatmap(days=3650, bucket="hour")
    assert any(r["slot"] == 14 for r in hours)
    dows = db.heatmap(days=3650, bucket="dow")
    assert dows and 0 <= dows[0]["slot"] <= 6


def test_samples_between_filters_target(db):
    db.import_samples([
        {"ts": "2026-10-20T09:00:00", "target": "A", "ok": 1, "ms": 10.0},
        {"ts": "2026-10-20T09:00:01", "target": "B", "ok": 1, "ms": 20.0},
    ])
    everything = db.samples_between("2000-01-01", "2100-01-01")
    assert len(everything) == 2
    only_b = db.samples_between("2000-01-01", "2100-01-01",
                                [r["target_id"] for r in everything if r["target"] == "B"])
    assert len(only_b) == 1 and only_b[0]["target"] == "B"


# ------------------------- link notes -------------------------
def test_list_notes_and_summary(db):
    db.import_samples([
        {"ts": "2026-10-20T08:00:00", "target": "A", "ok": 1, "ms": 40.0},
        {"ts": "2026-10-20T08:00:01", "target": "A", "ok": 1, "ms": 44.0},
    ])
    # 给最近那个会话打上链路备注
    sid = db.list_sessions(limit=1)[0]["id"]
    db.update_session(sid, note="有线")

    db.import_samples([
        {"ts": "2026-10-20T09:00:00", "target": "A", "ok": 1, "ms": 200.0},
        {"ts": "2026-10-20T09:00:01", "target": "A", "ok": 0, "ms": None},
    ])
    sid2 = db.list_sessions(limit=1)[0]["id"]
    db.update_session(sid2, note="手机热点")

    notes = {n["note"]: n for n in db.list_notes()}
    assert set(notes) == {"有线", "手机热点"}
    assert notes["有线"]["samples"] == 2

    wired = db.note_summary("有线")[0]
    assert wired["avg"] == 42.0 and wired["loss"] == 0.0
    mobile = db.note_summary("手机热点")[0]
    assert mobile["avg"] == 200.0 and mobile["loss"] == 50.0


def test_daily_trend_filters_by_note(db):
    db.import_samples([{"ts": "2026-10-20T08:00:00", "target": "A", "ok": 1, "ms": 40.0}])
    db.update_session(db.list_sessions(limit=1)[0]["id"], note="有线")
    db.import_samples([{"ts": "2026-10-20T09:00:00", "target": "A", "ok": 1, "ms": 200.0}])
    db.update_session(db.list_sessions(limit=1)[0]["id"], note="手机热点")

    only_wired = db.daily_trend(days=3650, note="有线")
    assert len(only_wired) == 1 and only_wired[0]["avg"] == 40.0
    everything = db.daily_trend(days=3650)
    assert len(everything) == 1              # 同一天同目标仍是一行（合并后均值）
    assert everything[0]["sent"] == 2


def test_notes_ignore_empty(db):
    db.import_samples([{"ts": "2026-10-20T08:00:00", "target": "A", "ok": 1, "ms": 40.0}])
    assert db.list_notes() == []             # 未填备注的会话不进列表


# ------------------------- import -------------------------
def test_import_creates_targets(db):
    r = db.import_samples([
        {"ts": "2026-10-20T09:00:00", "target": "全新平台", "ok": 1, "ms": 55.0},
        {"ts": "2026-10-20T09:00:02", "target": "全新平台", "ok": 1, "ms": 57.0},
    ])
    assert r["imported"] == 2
    assert "全新平台" in r["created_targets"]
    sess = db.get_session(r["session_id"])
    assert sess["status"] == "done"
    assert sess["rounds_done"] == 2


def test_import_empty_is_noop(db):
    assert db.import_samples([]) == {"session_id": None, "imported": 0,
                                     "created_targets": []}


def test_import_rejects_missing_target(db):
    with pytest.raises(ValueError):
        db.import_samples([{"ts": "2026-10-20T09:00:00", "ok": 1, "ms": 10.0}])


def test_import_ok_flag_variants(db):
    r = db.import_samples([
        {"ts": "2026-10-20T09:00:00", "target": "A", "ok": "0", "ms": ""},
        {"ts": "2026-10-20T09:00:01", "target": "A", "ok": True, "ms": 12.0},
    ])
    rows = db.fetch_samples(r["session_id"])
    assert [bool(x["ok"]) for x in rows] == [False, True]


# ------------------------- report -------------------------
def test_report_is_self_contained(db):
    r = db.import_samples([
        {"ts": "2026-10-20T09:00:00", "target": "A", "ok": 1, "ms": 45.0},
        {"ts": "2026-10-20T09:00:05", "target": "A", "ok": 0, "ms": None},
    ])
    page = build_report(db, r["session_id"])
    assert page.startswith("<!DOCTYPE html>")
    assert "http://" not in page and "https://" not in page
    assert "<script src" not in page                  # 无外链脚本
    assert "A" in page


def test_report_missing_session(db):
    with pytest.raises(KeyError):
        build_report(db, 99999)
