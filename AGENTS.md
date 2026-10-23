# AGENTS.md

面向接手本仓库的 AI 代理（以及未来的自己）。这里只写**看代码不容易看出来、但做错会白跑半天**的东西；
功能说明看 [README.md](README.md) / [README.zh-CN.md](README.zh-CN.md)，不要在这里重复。

---

## 1. 这是什么

游戏平台网络延迟监控：对完美、5E、无畏契约、Steam、Epic、战网等目标做定时探测，
落 SQLite，带 Web 界面、定时任务、阈值告警、卡顿分析与导出。

单进程 Python（FastAPI）+ 零构建前端（一个 HTML/CSS/JS）。没有 Node 构建步骤，
不要引入打包器、npm 依赖或前端框架——那是刻意的设计选择，不是欠债。

---

## 2. 环境与命令

**用项目内 venv，不要用 PATH 里的 `python`。** 本机 PATH 上的 `python` 可能被别的软件顶掉
（例如 MySQL Workbench 自带的 `python.exe` 桩：没有 pip、没有包，还会打印
`Could not find platform independent libraries <prefix>`）。

```bash
python -m venv .venv                      # 首次
.\.venv\Scripts\Activate.ps1              # PowerShell（cmd: .venv\Scripts\activate.bat）
pip install -r requirements.txt
pip install pytest httpx2                 # 测试依赖（httpx2 见 §7.6）

# 也可以不激活，直接点名解释器
.\.venv\Scripts\python.exe run.py
.\.venv\Scripts\python.exe run.py serve --port 9000
```

常用命令：

| 目的 | 命令 |
| --- | --- |
| 起服务（默认 127.0.0.1:8848） | `python run.py` 或 `python -m gameping serve` |
| 无界面跑一轮并入库 | `python -m gameping test -t 60 -i 5` |
| 跨会话趋势（不开网页） | `python -m gameping analyze --days 30` |
| 导出单文件 HTML 报告 | `python -m gameping report -s 12 -o out.html` |
| 全部测试 | `.\.venv\Scripts\python.exe -m pytest -q` |
| 界面冒烟 + 截图 | `cd .verify && npm i && node verify.js`（需本机 Chrome） |

`python -m gameping …` 与控制台脚本 `gameping` 需要 `pip install -e .`；`run.py` 不需要。

---

## 3. 模块职责（依赖只能向下）

```
api.py ──┬─→ runner.py ─→ probe.py
         ├─→ scheduler.py ─→ db.py ─→ config.py
         ├─→ alerts.py  ─→ db.py
         ├─→ report.py  ─→ stats.py
         └─→ export.py  ─→ stats.py
cli.py ─→ db.py / runner.py / report.py
```

| 文件 | 职责 | 约束 |
| --- | --- | --- |
| `probe.py` | 四种探测：`tcp` / `icmp` / `dns` / `http` | 纯函数，不碰数据库 |
| `stats.py` | 统计与评分：百分位、评级、分桶、趋势、抖动点、卡顿分、基线 | **纯函数，禁止 import db** |
| `db.py` | SQLite 表结构与 DAO（WAL、线程本地连接、`_migrate`） | 唯一写库的地方 |
| `runner.py` | 单会话后台线程、每轮并发探测、内存增量聚合 | 同时只允许一个会话 |
| `scheduler.py` | 每日 HH:MM 定时线程 | 迟到超过 30 分钟不补跑 |
| `alerts.py` | 会话结束后的阈值判定 | best-effort，失败不影响会话状态 |
| `report.py` / `export.py` | 自包含 HTML 报告 / 独立 SVG 图表 | 输出必须单文件、无外链 |
| `api.py` | FastAPI 路由 + 静态托管 | 见 §7.1 的模型位置坑 |
| `web/` | index.html · app.js · style.css · sw.js · manifest | 无构建、无外部 CDN |

---

## 4. 数据模型

`target`（目标：host/port/proto/baseline）→ `sample`（每轮每目标一行：ts/ok/ms/ip/dot）
→ 归属 `session`（一次测试）；另有 `schedule`（每日计划）、`alert`（告警）、`instance`（多机来源）、`meta`。

- `sample.ms` 为 `NULL` 表示**超时/丢包**，不是 0。所有统计都靠这个区分。
- `session.note` 是用户填的链路备注（「有线」「手机热点」），用于按链路对比；导入时不要用来源标记污染它。
- `target.baseline` 是该平台的「正常延迟」，用于 ±20% / +50% 标注。
- `dot` 记录本次实际用的地址族（`v4`/`v6`）。

---

## 5. 硬性约定

1. **删除一律进 `.trash/`**，不要 `rm -f`、`Remove-Item -Force`、`git rm --cached` 清库。
   `.trash/` 已在 `.gitignore` 里。
2. **`data/` 不上仓库**（只有 `.gitkeep` 占位）。里面有用户真实测试历史，属于隐私数据。
3. **推送前必须获得用户明确许可**；并且推送前确认没有绝对路径、令牌、机器名混进提交
   （本仓库有 `.verify/scan_secrets.py` 做这个检查，会跳过自身）。
4. **一个功能的 bug 修完再提交**，不要一个问题一个提交。
5. 注释与 UI 文案用中文，标识符用英文。
6. **一次性探索脚本不要留在根目录**：有效逻辑合并进 `.verify/verify.js`，残骸移入
   `.verify/_attic/` 并在其 README 里写清「已并入哪一项、勿直接运行」。
7. 前端改动后必须让 `.verify/verify.js` 通过，且控制台零报错。

---

## 6. 测试与验收

- `tests/` 共 111 项，分四个文件：`test_core.py`（统计/探测/DB/调度）、
  `test_features.py`（新能力）、`test_api.py`、`test_api_features.py`（接口层）。
- 改动 `stats.py` 的评分或分桶逻辑时，**同时改断言**，不要为了让测试变绿去调低标准。
- 真实网络相关断言只允许打到 `127.0.0.1` 或字面 IP（`223.5.5.5` 这类），
  不要让测试依赖某个游戏平台域名是否可达。
- 界面验收项：实时卡片数、曲线 path 非空、悬停提示有时间戳、热力图有格子、
  DNS 行数、实例行数、SVG 可解析、PWA 资源 200、移动端无横向溢出。

---

## 7. 踩过的坑（按踩中概率排序）

### 7.1 Pydantic 模型必须定义在模块级

`api.py` 顶部有 `from __future__ import annotations`。如果为了「局部性」把
`class XxxReq(BaseModel)` 写进 `create_app()` 里，FastAPI 解析注解会退回按查询参数处理，
表现为 **422 `{"loc":["query","req"],"msg":"Field required"}`**，而且看起来像前端没传参。
所有请求体模型都放在模块顶部。

### 7.2 SVG 必须显式设 `viewBox`

`#chartSvg` 在 HTML 里没有 `viewBox`，`drawChart()` 里必须
`svg.setAttribute('viewBox', '0 0 1000 260')` 并 `preserveAspectRatio='none'`。
漏了这步坐标会被按 CSS 像素拉伸，**曲线看不见但 path 数据是对的**——很容易误判成数据问题。

### 7.3 稀疏数据不能每个点都 `M`

分桶后大部分桶是空的。若每个有效点都起 `M`，折线会退化成一片孤立点。
正确做法是收集非空点后连续 `L`；单点情况画一个微小线段或圆点，否则不可见。

### 7.4 悬停要显示「那一次」，不是桶均值

`bucketize()` 每个桶除了 `avg` 还带 `samples`（原始读数，含 `ms: null` 的超时）。
悬停读数取桶内离光标最近的那条样本，否则用户看到的是平均值，和「查那一下具体多少 ms」的诉求不符。

### 7.5 SQLite 加列没有 `IF NOT EXISTS`

升级旧库用 `_migrate()` + `_add_column()`（先 `PRAGMA table_info` 查，再 `ALTER TABLE`）。
表级 `UNIQUE` 只能建表时给，旧库要先去重再 `CREATE UNIQUE INDEX`。

### 7.6 新版 starlette 的 TestClient 需要 `httpx2`

依赖清单里只写 fastapi/uvicorn 时，`./.venv/Scripts/python -m pytest` 会在**收集阶段**报
`starlette.testclient module requires httpx2`。`pyproject.toml` 的 dev extras 已含它。

### 7.7 `.venv` 可能指向已删除的解释器

症状：`.venv\Scripts\python.exe -V` 报
`did not find executable at ...\Python315\python.exe`。
原因是 `pyvenv.cfg` 的 `home` 指着一个后来被删/残缺的 Python。
处置：`mv .venv .trash/venv-broken && python -m venv .venv && pip install -r requirements.txt pytest httpx2`。

### 7.8 推送卡死：GCM 弹窗在等人工选账户

本机 Git Credential Manager 里存有多个 GitHub 账户，系统 `credential.helper` 走 GCM 时会弹窗让人选账户。
命令行下没人点 → `git push` 零输出挂到超时，**看起来和网络超时一模一样**。
绕过办法是清空继承链、只用插件令牌 helper：

```bash
HTTPS_PROXY= HTTP_PROXY= NO_PROXY='*' \
git -c credential.helper= \
    -c credential.helper='!python <helper>.py' push origin main
```

另外 `gh` 走本机 HTTP 代理时会 TLS 握手超时，命令行 git/gh 都要先清掉代理环境变量。
排查用 `GIT_TRACE=1`；**不要用 `GIT_CURL_VERBOSE`**，它会打印 Authorization 头泄露令牌。

### 7.9 两个「不是 bug」的观感问题

- `steamcommunity.com` 在默认网络下本就不通，显示 `X / 100% 丢包` 是正常的。
- 多目标并发会抬高读数（十几个 TCP 握手抢同一份资源）；有线/Wi-Fi/手机热点之间的
  基线差异可达 4 倍，**跨链路不可横向比较**，要对比得看 `session.note` 相同的数据。

---

## 8. 已知限制

- 测的是到**边缘节点/CDN** 的延迟，不是游戏内对局服务器。要测对局服需换成区域服地址。
- 浏览器无法发 ICMP、也无法对任意端口建连。若要做纯前端模式，只能用
  `Image()` 计时或 `fetch(no-cors)` 计时，测到的是**应用层 RTT**（含 TLS 与服务端处理）。
  实测同一批主机：TCP 握手 49ms 对浏览器 Image 计时 311ms，差约 6 倍，
  两种模式的数字不能混着比。（实验留档：`.verify/_attic/_browser-ping.js`）

---

## 9. 提交约定

- 提交信息用中文，一句话说清做了什么；改动多时列要点。
- 一处修复涉及多个文件就一次提交，不要拆成「改代码」「补测试」两个提交。
- 提交前：`pytest -q` 全绿 + `.verify/scan_secrets.py` 无命中。
- 推送需要用户点头（见 §5.3）。
