"""API tests using FastAPI's TestClient (no network needed except loopback)."""
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


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["ok"] is True


def test_index_and_static(client):
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200


def test_targets_list_and_crud(client):
    items = client.get("/api/targets").json()["items"]
    assert len(items) >= 8
    assert "ip" in items[0]

    r = client.post("/api/targets", json={"name": "测试站", "host": "127.0.0.1",
                                          "port": 80, "proto": "tcp", "group": "测试"})
    assert r.status_code == 201
    tid = r.json()["id"]

    assert client.patch(f"/api/targets/{tid}", json={"enabled": 0}).json()["enabled"] == 0
    assert client.delete(f"/api/targets/{tid}").status_code == 200
    assert client.delete(f"/api/targets/{tid}").status_code == 404


def test_start_requires_valid_clock(client):
    r = client.post("/api/session/start", json={"duration": 0, "rounds": 0})
    assert r.status_code == 400


def test_start_stop_cycle(client):
    tid = client.get("/api/targets").json()["items"][0]["id"]
    r = client.post("/api/session/start",
                    json={"duration": 0, "rounds": 3, "interval": 0,
                          "timeout": 0.3, "target_ids": [tid]})
    assert r.status_code == 201
    sid = r.json()["session_id"]

    deadline = time.time() + 20
    while time.time() < deadline:
        live = client.get("/api/session/live").json()
        if not live["running"]:
            break
        time.sleep(0.3)
    assert live["rounds_done"] >= 1

    detail = client.get(f"/api/sessions/{sid}").json()
    assert detail["summary"]
    assert client.get(f"/api/sessions/{sid}/series").status_code == 200
    assert client.get(f"/api/sessions/{sid}/export.csv").status_code == 200
    assert client.get(f"/api/sessions/{sid}/export.json").status_code == 200
    assert client.delete(f"/api/sessions/{sid}").status_code == 200


def test_second_start_conflicts(client):
    tid = client.get("/api/targets").json()["items"][0]["id"]
    body = {"duration": 5, "interval": 1, "timeout": 1, "target_ids": [tid]}
    assert client.post("/api/session/start", json=body).status_code == 201
    assert client.post("/api/session/start", json=body).status_code == 409
    client.post("/api/session/stop")


def test_compare_and_missing_targets(client):
    tid = client.get("/api/targets").json()["items"][0]["id"]
    client.post("/api/session/start",
                json={"duration": 0, "rounds": 2, "interval": 0,
                      "timeout": 0.3, "target_ids": [tid]})
    time.sleep(2.5)
    sid = client.get("/api/sessions").json()["items"][0]["id"]
    r = client.get(f"/api/sessions/{sid}/compare?a={tid}&b=99999")
    assert r.status_code == 404


def test_config_roundtrip(client):
    assert client.get("/api/config").json()["config"]["interval"]
    r = client.patch("/api/config", json={"interval": 3.0})
    assert r.json()["config"]["interval"] == 3.0


def test_purge_endpoint(client):
    assert client.post("/api/maintenance/purge?days=90").json()["removed_sessions"] == 0
