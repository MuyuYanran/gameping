"""Configuration and paths for GamePing."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("GAMEPING_DATA_DIR", PROJECT_ROOT / "data"))
WEB_DIR = Path(__file__).resolve().parent / "web"
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "gameping.db"

DEFAULT_CONFIG: dict[str, Any] = {
    "interval": 5.0,
    "timeout": 1.0,
    "default_duration": 300,
    "with_icmp": False,
    "retention_days": 90,
    "max_workers": 16,
    "dns_cache_seconds": 300,
    # 告警阈值：会话结束后按此判定，超标的落库并在界面提示
    "thresholds": {"latency": 100.0, "loss": 5.0, "jitter": 50.0},
}

# 内置目标。name / host / port / proto
DEFAULT_TARGETS: list[dict[str, Any]] = [
    {"name": "完美世界·竞技", "host": "pvp.wanmei.com", "port": 443, "proto": "tcp", "group": "CS2 平台"},
    {"name": "完美世界·CSGO", "host": "csgo.wanmei.com", "port": 443, "proto": "tcp", "group": "CS2 平台"},
    {"name": "5E·官网", "host": "www.5eplay.com", "port": 443, "proto": "tcp", "group": "CS2 平台"},
    {"name": "5E·平台接口", "host": "api.5eplay.com", "port": 443, "proto": "tcp", "group": "CS2 平台"},
    {"name": "无畏契约·官网", "host": "valorant.qq.com", "port": 443, "proto": "tcp", "group": "FPS 平台"},
    {"name": "Steam·商店", "host": "store.steampowered.com", "port": 443, "proto": "tcp", "group": "客户端商店"},
    {"name": "Steam·社区", "host": "steamcommunity.com", "port": 443, "proto": "tcp", "group": "客户端商店"},
    {"name": "Epic·商店", "host": "store.epicgames.com", "port": 443, "proto": "tcp", "group": "客户端商店"},
    {"name": "战网·国服", "host": "www.battlenet.com.cn", "port": 443, "proto": "tcp", "group": "客户端商店"},
    {"name": "腾讯游戏", "host": "game.qq.com", "port": 443, "proto": "tcp", "group": "通用"},
    {"name": "阿里DNS·基准", "host": "223.5.5.5", "port": 53, "proto": "tcp", "group": "基准"},
]


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_dirs()
    cfg = dict(DEFAULT_CONFIG)
    if CONFIG_PATH.exists():
        try:
            cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    return cfg


def save_config(patch: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    for key, value in patch.items():
        if key in DEFAULT_CONFIG:
            cfg[key] = value
    ensure_dirs()
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return cfg
