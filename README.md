# GamePing

**Game platform network latency monitor** — scheduled TCP/ICMP/DNS/HTTP probing against game platforms (Perfect World, 5E, VALORANT, Steam, Epic, Battle.net …), with a web UI, SQLite history, scheduled runs, threshold alerts and analysis tooling.

[中文文档](README.zh-CN.md)

![live dashboard](docs/screenshot-live.png)

## Features

- **Four probe modes** — `tcp` (TCP handshake, default), `icmp` (system ping), `dns` (fresh resolution time), `http` (time-to-first-byte). TCP is the default because it works against any HTTPS port and is unaffected by servers that block ICMP.
- **Live dashboard** — per-target average / min / P95 / jitter / loss, sparkline, S→X grade, global best/worst/KPI strip, progress bar.
- **SQLite history** — every round is persisted. Sessions survive restarts and the dashboard reattaches to the last session automatically.
- **Scheduled runs** — define "every day at HH:MM for N minutes"; a background scheduler starts the test automatically. Missed occurrences beyond a 30-minute grace window are *not* back-filled (a laptop waking at 09:00 must not fire six tests).
- **Threshold alerts** — after each session, targets exceeding latency / loss / jitter thresholds produce a stored alert with an unread badge in the header.
- **Analysis tools** — latency curves (single session or any time window, multi-target overlay), trend (first-third vs last-third), MAD-based spike detection, two-target head-to-head scoring, per-day cross-session trends, and a loss heatmap by hour or weekday.
- **Link tagging / link comparison** — label a session with a free-text network note ("wired", "phone hotspot"); the analysis page can then compare two links target-by-target side by side.
- **Self-contained HTML report** — one file, inline SVG, no external assets; safe to send to a friend.
- **Import** — bring in external CSV/JSON samples; unknown targets are created automatically.
- **Export** — CSV (Excel-friendly BOM) and JSON per session; retention purge.
- **Zero front-end dependencies** — one HTML, one CSS, one JS file, served by the same FastAPI app.

![analysis](docs/screenshot-analysis.png)

## Quick start

```bash
pip install -r requirements.txt          # fastapi + uvicorn

python -m gameping serve                # http://127.0.0.1:8848
python -m gameping serve --port 9000    # custom port
```

From the project root you can also run `python run.py`.

## Command line

```bash
gameping test -t 60 -i 5                 # 60 s headless run, writes to the same DB
gameping test -n 10 --targets 5E·官网    # 10 rounds, one target
gameping analyze --days 30               # cross-session trend table, no web server
gameping report -s 12 -o out.html        # self-contained HTML report
```

## Using the UI

| Page | What it does |
| --- | --- |
| 实时测试 / Live | Duration + interval + targets, start/stop, live cards |
| 历史记录 / History | Session list, per-target summary, CSV/JSON/HTML export, delete |
| 分析工具 / Analysis | Curves, trend, spikes, heatmap, cross-session trend, A/B compare |
| 定时与告警 / Schedules | Daily plans, alert log, CSV import |
| 目标管理 / Targets | CRUD, enable/disable, one-shot probe, resolved IP, probe mode |

![schedules](docs/screenshot-schedules.png)

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | health + version |
| GET | `/api/targets?all=1` | list targets with resolved IP |
| POST | `/api/targets` | add target `{name,host,port,proto,group}` |
| PATCH | `/api/targets/{id}` | partial update |
| DELETE | `/api/targets/{id}` | delete target and its samples |
| POST | `/api/targets/{id}/probe` | single probe right now |
| POST | `/api/session/start` | `{duration,interval,timeout,rounds,target_ids}` |
| POST | `/api/session/stop` | stop the running session |
| GET | `/api/session/live` | live state + per-target stats |
| GET | `/api/sessions` | history list |
| GET | `/api/sessions/{id}` | session detail + summary |
| GET | `/api/sessions/{id}/series` | bucketed series, trend, spikes |
| GET | `/api/sessions/{id}/compare?a=&b=` | head-to-head |
| GET | `/api/sessions/{id}/export.csv\|json` | download |
| GET | `/api/sessions/{id}/report.html` | self-contained report (`?download=1` to save) |
| DELETE | `/api/sessions/{id}` | delete a session |
| POST | `/api/sessions/import` | import JSON samples |
| POST | `/api/sessions/import.csv` | import CSV (raw body, `text/csv`) |
| GET | `/api/analytics/trends?days=N` | per-day cross-session trend |
| GET | `/api/analytics/heatmap?days=N&bucket=hour\|dow` | loss heatmap |
| GET | `/api/analytics/window?start=&end=&target_ids=` | any time window |
| GET | `/api/analytics/notes` | session note (link) list |
| GET | `/api/analytics/notes/compare?a=&b=` | compare two links |
| GET/POST | `/api/schedules` | list / create daily plans |
| PATCH/DELETE | `/api/schedules/{id}` | update / delete a plan |
| POST | `/api/schedules/{id}/run` | run a plan immediately |
| GET | `/api/alerts` | alert log + unread count + thresholds |
| POST | `/api/alerts/seen` | mark read (all, or one by `alert_id`) |
| POST | `/api/alerts/check/{session_id}` | re-evaluate a session |
| POST | `/api/maintenance/purge?days=N` | retention cleanup |
| GET | `/api/stats/overview` | DB counters |

Interactive docs: `http://127.0.0.1:8848/docs`.

## Project layout

```
gameping/
├── run.py                    # convenience launcher
├── requirements.txt
├── pyproject.toml
├── src/gameping/
│   ├── config.py             # paths, defaults, built-in targets, thresholds
│   ├── probe.py              # tcp / icmp / dns / http probes, DNS cache
│   ├── stats.py              # pure stats: percentile, grade, buckets, trend, spikes
│   ├── db.py                 # SQLite schema + DAO (target/session/sample/schedule/alert)
│   ├── runner.py             # background session loop, live aggregate
│   ├── scheduler.py          # daily HH:MM scheduler thread
│   ├── alerts.py             # threshold evaluation
│   ├── report.py             # self-contained HTML report
│   ├── api.py                # FastAPI routes + static hosting
│   ├── cli.py                # serve / test / analyze / report
│   └── web/                  # index.html · style.css · app.js · favicon.svg
├── tests/                    # 69 pytest cases
├── .verify/                  # Playwright UI smoke script (not shipped)
└── data/                     # SQLite database + config (git-ignored)
```

## Grading

| Grade | Average RTT | Note |
| --- | --- | --- |
| S | ≤ 40 ms | excellent for competitive FPS |
| A | ≤ 70 ms | good |
| B | ≤ 110 ms | playable |
| C | > 110 ms | laggy |
| X | — | all probes failed |

Any loss demotes one grade; ≥ 25 % loss caps at C; ≥ 50 % loss is X. Jitter > 50 ms also demotes one grade.

## Configuration

`data/config.json` is created on first run and can be edited through `PATCH /api/config`:

```json
{
  "interval": 5.0,
  "timeout": 1.0,
  "retention_days": 90,
  "max_workers": 16,
  "thresholds": { "latency": 100.0, "loss": 5.0, "jitter": 50.0 }
}
```

`GAMEPING_DATA_DIR` overrides the data directory.

## Tests

```bash
pip install pytest
PYTHONPATH=src python -m pytest -q     # 69 passed
```

UI smoke check (optional, needs a local Chrome):

```bash
cd .verify && npm i && node verify.js  # screenshots into .verify/shots/
```

## Notes & limits

- **The measured baseline is whatever link you are on.** Wired, Wi-Fi and phone-tethered hotspot give very different RTTs — a mobile hotspot commonly sits at 150–250 ms where the same targets read 45–70 ms over wired fibre. Tag each run with a link note so you compare like with like.
- **Probe concurrency inflates measured latency.** Eleven simultaneous TCP connects compete for the same socket budget, so a multi-target sweep reads higher than a single `ping`. If you need absolute numbers, test fewer targets per session.
- TCP probing measures handshake RTT to the **edge/CDN**, not the in-game match server. Use the game's own region list for match-server RTT.
- `steamcommunity.com` is unreachable from mainland China by default — it shows X / 100 % loss, which is expected, not a bug.
- ICMP mode shells out to the system `ping` and needs the usual permissions.
- Platforms rotate CDN hostnames; verify the resolved IP on the Targets page before trusting a target.
