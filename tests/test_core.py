"""Tests for GamePing. Run with: pytest -q  (or python -m pytest)"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gameping import stats as S            # noqa: E402
from gameping.db import Database          # noqa: E402
from gameping.probe import ProbeTarget, resolve, tcp_ping  # noqa: E402
from gameping.runner import Runner        # noqa: E402


# ------------------------- stats -------------------------
def test_summarize_basic():
    st = S.summarize([10.0, 20.0, 30.0, 40.0], sent=4, target="t")
    assert st.sent == 4 and st.recv == 4
    assert st.loss == 0.0
    assert st.avg == 25.0
    assert st.mn == 10.0 and st.mx == 40.0
    assert st.med == 25.0
    assert st.grade == "S"
    assert st.spark == [10.0, 20.0, 30.0, 40.0]      # 保持时间序


def test_summarize_with_loss():
    st = S.summarize([50.0, 60.0], sent=4)
    assert st.loss == 50.0
    assert st.recv == 2
    assert st.grade == "X"                 # 一半丢包 = 不可用


def test_grade_degrades_stepwise():
    st = S.summarize([50.0] * 4, sent=5)   # 20% 丢包，延迟本来 A
    assert st.loss == 20.0
    assert st.grade == "B"                 # 有丢包降一级
    st2 = S.summarize([50.0] * 3, sent=4)  # 25% 丢包 → 封顶 C
    assert st2.grade == "C"


def test_summarize_all_lost():
    st = S.summarize([], sent=5)
    assert st.grade == "X"
    assert st.avg is None and st.loss == 100.0


def test_grade_thresholds():
    assert S.grade_of(20, 0, 5) == "S"
    assert S.grade_of(65, 0, 5) == "A"
    assert S.grade_of(100, 0, 5) == "B"
    assert S.grade_of(200, 0, 5) == "C"
    assert S.grade_of(None, 0, 0) == "X"
    assert S.grade_of(20, 30, 5) == "C"     # 25%~50% 丢包封顶 C
    assert S.grade_of(20, 60, 5) == "X"     # 过半丢包直接不可用
    assert S.grade_of(20, 0, 80) == "A"     # 抖动降一级


def test_percentile():
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert S.percentile(vals, 0.0) == 1
    assert S.percentile(vals, 1.0) == 10
    assert S.percentile(vals, 0.5) == 6


def test_bucketize_and_trend():
    base = datetime.now()
    pts = [(base + timedelta(seconds=i), 10.0 + i) for i in range(60)]
    pts[10] = (pts[10][0], None)
    buckets = S.bucketize(pts, buckets=6)
    assert len(buckets) >= 1
    tr = S.trend(pts)
    assert tr["direction"] in {"up", "down", "flat"}
    assert tr["delta"] is not None


def test_bucketize_keeps_raw_samples():
    """悬停要显示「那一次」的读数，桶里必须带原始采样。"""
    base = datetime.now()
    pts = [(base + timedelta(seconds=i), 40.0 + i) for i in range(6)]
    pts[2] = (pts[2][0], None)                       # 一次超时
    buckets = S.bucketize(pts, buckets=3)
    flat = [s for b in buckets for s in b["samples"]]
    assert len(flat) == 6                             # 一条不丢，包括超时点
    assert sum(1 for s in flat if s["ms"] is None) == 1
    assert all("t" in s for s in flat)
    assert buckets[0]["avg"] == 40.5                  # 均值只算成功样本


def test_bucketize_single_point_has_sample():
    base = datetime.now()
    out = S.bucketize([(base, 55.5)], buckets=5)
    assert len(out) == 1
    assert out[0]["samples"] == [{"t": base.isoformat(timespec="seconds"), "ms": 55.5}]


def test_bucketize_sparse_buckets_are_empty():
    base = datetime.now()
    pts = [(base, 10.0), (base + timedelta(seconds=100), 20.0)]
    out = S.bucketize(pts, buckets=10)
    assert len([b for b in out if b["samples"]]) == 2
    assert all(b["avg"] is None for b in out if not b["samples"])


def test_spikes_detects_outlier():
    base = datetime.now()
    pts = [(base + timedelta(seconds=i), 20.0) for i in range(30)]
    pts[15] = (pts[15][0], 400.0)
    found = S.spikes(pts)
    assert found and found[0]["ms"] == 400.0


def test_trend_flat_needs_enough_points():
    base = datetime.now()
    assert S.trend([(base, 10.0)] * 3)["direction"] == "flat"


def test_compare_picks_lower_score():
    a = S.summarize([20, 21, 22], sent=3, target="a")
    b = S.summarize([90, 95, 100], sent=3, target="b")
    assert S.compare(a, b)["winner"] == "a"


# ------------------------- db -------------------------
@pytest.fixture()
def db(tmp_path):
    d = Database(tmp_path / "t.db")
    yield d
    d.close()


def test_seed_targets(db):
    items = db.list_targets()
    assert len(items) >= 8
    assert all(t["enabled"] == 1 for t in items)


def test_target_crud(db):
    t = db.add_target("自定义", "example.com", 80, "tcp", "测试")
    assert t["name"] == "自定义"
    upd = db.update_target(t["id"], port=8080, enabled=0)
    assert upd["port"] == 8080 and upd["enabled"] == 0
    assert db.delete_target(t["id"])
    assert db.get_target(t["id"]) is None


def test_session_and_samples(db):
    first = db.list_targets()[0]
    sid = db.create_session(duration=10, interval=1, timeout=1, target_ids=[first["id"]],
                            rounds=0, note="t")
    assert sid > 0
    n = db.add_samples(sid, 1, [
        {"target_id": first["id"], "ts": datetime.now().isoformat(timespec="seconds"),
         "ok": True, "ms": 12.5, "ip": "1.2.3.4"},
        {"target_id": first["id"], "ts": datetime.now().isoformat(timespec="seconds"),
         "ok": False, "ms": None, "ip": "1.2.3.4"},
    ])
    assert n == 2
    rows = db.fetch_samples(sid)
    assert len(rows) == 2
    summ = db.session_summary(sid)
    assert summ[0]["sent"] == 2 and summ[0]["recv"] == 1
    assert summ[0]["loss"] == 50.0
    db.update_session(sid, status="done", rounds_done=1, finished_at="x")
    assert db.get_session(sid)["status"] == "done"
    assert db.delete_session(sid)
    assert db.get_session(sid) is None


def test_overview_counts(db):
    o = db.stats_overview()
    assert o["targets"] >= 8
    assert o["sessions"] == 0


def test_purge_removes_old(db):
    sid = db.create_session(duration=1, interval=1, timeout=1, target_ids=[1])
    with db.tx() as c:
        c.execute("UPDATE session SET started_at=? WHERE id=?",
                  ((datetime.now() - timedelta(days=30)).isoformat(timespec="seconds"), sid))
    assert db.purge_older_than(7) == 1
    assert db.get_session(sid) is None


# ------------------------- probe -------------------------
def test_resolve_localhost():
    assert resolve("localhost", ttl=0) in {"127.0.0.1", "::1"}


def test_tcp_ping_closed_port():
    ok, ms = tcp_ping("127.0.0.1", 1, timeout=0.3)
    assert ok is False and ms is None


def test_probe_target_reports_loss():
    t = ProbeTarget(id=1, name="x", host="127.0.0.1", port=1)
    t.refresh_ip()
    ok, ms = t.probe(0.3)
    assert (ok, ms) == (False, None)


# ------------------------- runner -------------------------
def test_runner_fixed_rounds(db):
    r = Runner(db)
    ids = [t["id"] for t in db.list_targets()[:2]]
    r.start(duration=0, interval=0, timeout=0.2, target_ids=ids, rounds=2)
    r.wait(20)
    assert not r.running
    assert r.state.rounds_done == 2
    sess = db.get_session(r.state.session_id)
    assert sess["status"] in {"done", "error"}
    assert sess["rounds_done"] == 2
    stats = r.live_stats()
    assert len(stats) == 2
    assert all(s["sent"] == 2 for s in stats)


def test_runner_rejects_second_start(db):
    r = Runner(db)
    ids = [db.list_targets()[0]["id"]]
    r.start(duration=0, interval=0, timeout=0.2, target_ids=ids, rounds=5)
    with pytest.raises(RuntimeError):
        r.start(duration=1, interval=1, timeout=1, target_ids=ids)
    r.stop()


def test_runner_rejects_empty_clock(db):
    r = Runner(db)
    with pytest.raises(RuntimeError):
        r.start(duration=0, interval=1, timeout=1, rounds=0)
