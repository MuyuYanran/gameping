"""API tests for the v1.1 endpoints (schedules, analytics, report, import, alerts)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from gameping.api import create_app  # noqa: E402


@pytest.fixture()
def client(tmp_path):
    app = create_app(str(tmp_path / "api.db"))
    with TestClient(app) as c:
        yield c


def _import(client, target="导入站", ms=(40.0, 42.0), bad=0):
    samples = []
    for i, v in enumerate(ms):
        samples.append({"ts": f"2026-10-20T09:00:{i:02d}", "target": target,
                        "ok": 1, "ms": v})
    for i in range(bad):
        samples.append({"ts": f"2026-10-20T09:01:{i:02d}", "target": target,
                        "ok": 0, "ms": None})
    return client.post("/api/sessions/import", json={"samples": samples})


# ------------------------- schedules -------------------------
def test_schedule_lifecycle(client):
    r = client.post("/api/schedules", json={"name": "早间", "at_time": "08:30",
                                            "duration": 120, "interval": 5,
                                            "timeout": 1, "target_ids": [1]})
    assert r.status_code == 201
    sch = r.json()
    assert sch["at_time"] == "08:30"

    assert client.get("/api/schedules").json()["running"] is True
    assert client.patch(f"/api/schedules/{sch['id']}",
                        json={"enabled": 0}).json()["enabled"] == 0
    assert client.delete(f"/api/schedules/{sch['id']}").status_code == 200
    assert client.delete(f"/api/schedules/{sch['id']}").status_code == 404


def test_schedule_rejects_bad_time(client):
    r = client.post("/api/schedules", json={"name": "x", "at_time": "99:99"})
    assert r.status_code in (400, 422)


def test_schedule_patch_validation(client):
    sch = client.post("/api/schedules", json={"name": "早间", "at_time": "08:30"}).json()
    assert client.patch(f"/api/schedules/{sch['id']}",
                        json={"at_time": "bad"}).status_code == 400
    assert client.patch("/api/schedules/99999", json={"enabled": 0}).status_code == 404


def test_schedule_run_now_starts_session(client):
    sch = client.post("/api/schedules", json={"name": "立即", "at_time": "23:59",
                                              "duration": 30, "interval": 2,
                                              "target_ids": [1]}).json()
    r = client.post(f"/api/schedules/{sch['id']}/run")
    assert r.status_code == 200
    assert r.json()["session_id"] >= 1
    client.post("/api/session/stop")


# ------------------------- analytics -------------------------
def test_trends_endpoint(client):
    _import(client)
    data = client.get("/api/analytics/trends?days=3650").json()
    assert data["series"]
    item = data["series"][0]
    assert item["target"] == "导入站"
    assert item["avg_overall"] is not None
    assert item["direction"] in {"up", "down", "flat"}


def test_heatmap_endpoints(client):
    _import(client)
    hour = client.get("/api/analytics/heatmap?days=3650&bucket=hour").json()
    assert hour["slots"] == 24 and len(hour["labels"]) == 24
    dow = client.get("/api/analytics/heatmap?days=3650&bucket=dow").json()
    assert dow["slots"] == 7
    assert hour["targets"][0]["cells"][9] is not None      # 导入样本在 09 点


def test_window_endpoint(client):
    _import(client)
    r = client.get("/api/analytics/window?start=2000-01-01T00:00:00"
                   "&end=2100-01-01T00:00:00&buckets=10")
    data = r.json()
    assert data["count"] >= 2
    assert data["series"][0]["points"]


def test_window_empty_range(client):
    data = client.get("/api/analytics/window?start=1990-01-01T00:00:00"
                      "&end=1990-01-02T00:00:00").json()
    assert data["series"] == [] and data["count"] == 0


def test_notes_and_link_compare(client):
    """导入时可以直接带 note，链路对比按 note 聚合。"""
    client.post("/api/sessions/import", json={
        "note": "有线",
        "samples": [{"ts": "2026-10-20T08:00:00", "target": "A", "ok": 1, "ms": 40.0},
                    {"ts": "2026-10-20T08:00:01", "target": "A", "ok": 1, "ms": 44.0}]})
    client.post("/api/sessions/import", json={
        "note": "手机热点",
        "samples": [{"ts": "2026-10-20T09:00:00", "target": "A", "ok": 1, "ms": 200.0},
                    {"ts": "2026-10-20T09:00:01", "target": "A", "ok": 0, "ms": None}]})

    notes = {n["note"] for n in client.get("/api/analytics/notes").json()["items"]}
    assert {"有线", "手机热点"} <= notes

    r = client.get("/api/analytics/notes/compare?a=有线&b=手机热点")
    assert r.status_code == 200, r.text
    row = r.json()["rows"][0]
    assert row["a_avg"] == 42.0
    assert row["b_avg"] == 200.0
    assert row["delta"] == 158.0              # B 比 A 慢 158ms
    assert row["b_loss"] == 50.0


def test_notes_compare_unknown(client):
    assert client.get("/api/analytics/notes/compare?a=x&b=y").status_code == 404


# ------------------------- stutter / bursts -------------------------
def test_stutter_endpoint(client):
    sid = _import(client, bad=2).json()["session_id"]
    r = client.get(f"/api/sessions/{sid}/stutter")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"]
    item = body["items"][0]
    assert "score" in item["stutter"] and 0 <= item["stutter"]["score"] <= 100
    assert isinstance(item["bursts"], list)
    assert body["worst"]


def test_stutter_missing_session(client):
    assert client.get("/api/sessions/99999/stutter").status_code == 404


def test_series_includes_stutter_and_bursts(client):
    sid = _import(client, bad=2).json()["session_id"]
    series = client.get(f"/api/sessions/{sid}/series").json()["series"]
    assert series
    first = series[0]
    assert "stutter" in first and "bursts" in first
    assert first["bursts"], "两次连续超时应被识别为一次突发"


# ------------------------- baseline -------------------------
def test_target_baseline_roundtrip(client):
    tid = client.get("/api/targets").json()["items"][0]["id"]
    r = client.patch(f"/api/targets/{tid}", json={"baseline": 45})
    assert r.status_code == 200 and r.json()["baseline"] == 45
    # 设了基线后，series 里应带上对比结果
    sid = _import(client).json()["session_id"]
    series = client.get(f"/api/sessions/{sid}/series").json()["series"]
    assert series, "导入的会话应有数据"


def test_target_baseline_clear(client):
    tid = client.get("/api/targets").json()["items"][0]["id"]
    client.patch(f"/api/targets/{tid}", json={"baseline": 40})
    r = client.patch(f"/api/targets/{tid}", json={"baseline": None})
    assert r.json()["baseline"] is None


# ------------------------- dns -------------------------
def test_dns_check_requires_host(client):
    assert client.get("/api/dns/check").status_code == 400


def test_dns_check_unknown_target(client):
    assert client.get("/api/dns/check?target_id=99999").status_code == 404


def test_dns_check_literal_ip(client):
    r = client.get("/api/dns/check?host=223.5.5.5").json()
    assert r["v4"] == ["223.5.5.5"] and r["suspicious"] is False


def test_dns_all_lists_targets(client):
    r = client.get("/api/dns/all").json()
    assert r["count"] >= 8
    assert all("v4" in item and "suspicious" in item for item in r["items"])


# ------------------------- instances -------------------------
def test_instance_lifecycle(client):
    r = client.post("/api/instances", json={"id": "desk", "label": "台式"})
    assert r.status_code == 201, r.text
    assert r.json()["id"] == "desk"
    assert any(i["id"] == "desk" for i in client.get("/api/instances").json()["items"])
    assert client.delete("/api/instances/desk").status_code == 200
    assert client.delete("/api/instances/desk").status_code == 404


def test_instance_import_records_source(client):
    r = client.post("/api/sessions/import", json={
        "instance": "laptop", "label": "笔记本",
        "samples": [{"ts": "2026-10-20T10:00:00", "target": "A", "ok": 1, "ms": 50.0}]})
    assert r.status_code == 200
    inst = {i["id"]: i for i in client.get("/api/instances").json()["items"]}
    assert "laptop" in inst and inst["laptop"]["samples"] >= 1


# ------------------------- svg export + pwa -------------------------
def test_chart_svg_endpoint(client):
    sid = _import(client).json()["session_id"]
    r = client.get(f"/api/sessions/{sid}/chart.svg")
    assert r.status_code == 200
    assert "image/svg+xml" in r.headers["content-type"]
    assert r.text.startswith("<svg")
    dl = client.get(f"/api/sessions/{sid}/chart.svg?download=1")
    assert dl.headers.get("content-disposition", "").startswith("attachment")


def test_chart_svg_missing_session(client):
    assert client.get("/api/sessions/9999/chart.svg").status_code == 404


def test_pwa_assets_served(client):
    assert client.get("/manifest.webmanifest").status_code == 200
    assert client.get("/sw.js").status_code == 200
    assert client.get("/static/icon-192.png").status_code == 200
    assert client.get("/static/icon-512.png").status_code == 200
    man = client.get("/manifest.webmanifest").json()
    assert man["display"] == "standalone"
    assert len(man["icons"]) == 2


def test_trends_note_filter(client):
    _import(client)
    r = client.get("/api/analytics/trends?days=3650&note=csv-import")
    assert r.status_code == 200
    assert r.json()["series"] == []          # 无备注的导入会话不会被该筛选命中
    assert client.get("/api/analytics/trends?days=3650").json()["series"]


# ------------------------- report -------------------------
def test_report_html(client):
    sid = _import(client, bad=1).json()["session_id"]
    r = client.get(f"/api/sessions/{sid}/report.html")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "GamePing" in r.text
    assert client.get(f"/api/sessions/{sid}/report.html?download=1") \
        .headers.get("content-disposition", "").startswith("attachment")
    assert client.get("/api/sessions/9999/report.html").status_code == 404


# ------------------------- import -------------------------
def test_import_json(client):
    r = _import(client)
    assert r.status_code == 200
    body = r.json()
    assert body["imported"] == 2
    assert client.get(f"/api/sessions/{body['session_id']}").status_code == 200


def test_import_rejects_empty(client):
    assert client.post("/api/sessions/import", json={"samples": []}).status_code == 400


def test_import_csv(client):
    csv_text = ("timestamp,target,ok,ms\r\n"
                "2026-10-20T08:00:00,CSV站,1,33.5\r\n"
                "2026-10-20T08:00:02,CSV站,0,\r\n")
    r = client.post("/api/sessions/import.csv", data=csv_text.encode("utf-8"),
                    headers={"Content-Type": "text/csv"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] == 2
    assert "CSV站" in body["created_targets"]


def test_import_csv_bom_and_alias_columns(client):
    csv_text = "\ufeffts,target_name,ok,ms\n2026-10-20T07:00:00,BOM站,1,12.0\n"
    r = client.post("/api/sessions/import.csv", data=csv_text.encode("utf-8"),
                    headers={"Content-Type": "text/csv"})
    assert r.status_code == 200, r.text


def test_import_csv_empty_body(client):
    r = client.post("/api/sessions/import.csv", data=b"timestamp,target,ok,ms\n",
                    headers={"Content-Type": "text/csv"})
    assert r.status_code == 400


# ------------------------- alerts -------------------------
def test_alerts_endpoints(client):
    sid = _import(client, ms=(500.0, 520.0)).json()["session_id"]
    created = client.post(f"/api/alerts/check/{sid}").json()["created"]
    assert any(c["kind"] == "latency" for c in created)
    listing = client.get("/api/alerts").json()
    assert listing["unseen"] >= 1
    assert "latency" in listing["thresholds"]
    assert client.post("/api/alerts/seen").json()["updated"] >= 1
    assert client.get("/api/alerts?unseen_only=true").json()["unseen"] == 0
    assert client.post("/api/alerts/check/9999").status_code == 404


def test_alert_fires_after_real_run(client):
    """A short real run against a closed port must end with a loss alert."""
    created = client.post("/api/targets", json={"name": "黑洞", "host": "127.0.0.1",
                                                "port": 1, "proto": "tcp"}).json()
    r = client.post("/api/session/start", json={"duration": 0, "rounds": 3, "interval": 0,
                                                "timeout": 0.2, "target_ids": [created["id"]]})
    assert r.status_code == 201
    deadline = time.time() + 20
    while time.time() < deadline:
        if not client.get("/api/session/live").json()["running"]:
            break
        time.sleep(0.3)
    alerts = client.get("/api/alerts").json()["items"]
    assert any(a["kind"] == "loss" and a["target"] == "黑洞" for a in alerts)


# ------------------------- overview -------------------------
def test_overview_has_alert_counter(client):
    body = client.get("/api/stats/overview").json()
    assert "unseen_alerts" in body
    assert body["unseen_alerts"] == 0
