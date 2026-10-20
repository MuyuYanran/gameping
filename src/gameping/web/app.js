'use strict';

/* ============================================================
   GamePing front-end. Vanilla JS, no build step.
   ============================================================ */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const GRADE_ORDER = ['S', 'A', 'B', 'C', 'X'];
const COLORS = ['#35d0ba', '#4c8dff', '#e8c341', '#f08a3c', '#a78bfa',
                '#ef5a6f', '#52c07a', '#22d3ee', '#fb7185', '#facc15', '#94a3b8', '#c084fc'];

const state = {
  view: 'live',
  targets: [],
  sessions: [],
  live: null,
  timer: null,
  chartSeries: [],
};

/* ---------------- utilities ---------------- */
async function api(path, opts = {}) {
  const isRaw = typeof opts.body === 'string';      // 已序列化的原始体（CSV 等）
  const res = await fetch(path, {
    ...opts,
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    body: opts.body === undefined || opts.body === null
      ? undefined
      : (isRaw ? opts.body : JSON.stringify(opts.body)),
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail || msg);
    } catch (_) {}
    throw new Error(msg);
  }
  return res.status === 204 ? null : res.json();
}

function toast(msg, isErr = false) {
  const box = $('#toast');
  const el = document.createElement('div');
  if (isErr) el.className = 'err';
  el.textContent = msg;
  box.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

const fmtMs = v => (v === null || v === undefined) ? '—' : `${Number(v).toFixed(1)}`;
const fmtPct = v => (v === null || v === undefined) ? '—' : `${Number(v).toFixed(1)}%`;
const pad2 = n => String(n).padStart(2, '0');

function fmtClock(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec % 3600 / 60), s = sec % 60;
  return h ? `${h}:${pad2(m)}:${pad2(s)}` : `${pad2(m)}:${pad2(s)}`;
}

function fmtTime(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return isNaN(d) ? iso : `${d.getMonth() + 1}/${d.getDate()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

function fmtDur(sec) {
  sec = Number(sec) || 0;
  if (sec >= 60) return `${Math.floor(sec / 60)}m${sec % 60 ? `${sec % 60}s` : ''}`;
  return `${sec}s`;
}

function gradeClass(g) { return `g-${GRADE_ORDER.includes(g) ? g : 'X'}`; }
function badge(g) { return `<span class="badge ${GRADE_ORDER.includes(g) ? g : 'X'}">${g || 'X'}</span>`; }

function sparkline(values, color = '#35d0ba', w = 240, h = 34, stamps = null) {
  if (!values || values.length < 2) {
    return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"></svg>`;
  }
  const max = Math.max(...values), min = Math.min(...values);
  const span = max - min || 1;
  const step = w / (values.length - 1);
  const cy = v => h - 3 - (v - min) / span * (h - 6);
  const pts = values.map((v, i) => `${(i * step).toFixed(1)},${cy(v).toFixed(1)}`);
  // 每个点挂原生 <title>：鼠标悬停即显示那一次的读数与时刻
  const dots = values.map((v, i) => {
    const when = stamps && stamps[i] ? ` @ ${fmtTime(stamps[i])}` : '';
    return `<circle cx="${(i * step).toFixed(1)}" cy="${cy(v).toFixed(1)}" r="2.2"
      fill="${color}" opacity="0"><title>第 ${i + 1} 次：${Number(v).toFixed(1)} ms${when}</title></circle>`;
  }).join('');
  return `<svg viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
    <polyline fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" points="${pts.join(' ')}"/>
    ${dots}
  </svg>`;
}

/* ---------------- navigation ---------------- */
function switchView(name) {
  state.view = name;
  $$('#nav button').forEach(b => b.classList.toggle('on', b.dataset.view === name));
  $$('.view').forEach(v => v.classList.toggle('on', v.id === `view-${name}`));
  if (name === 'history') loadSessions();
  if (name === 'analysis') { loadAnalysisSessions(); loadNotes(); }
  if (name === 'schedules') { loadSchedules(); }
  if (name === 'targets') loadTargets();
  if (name === 'live') loadTargetPicker();
}

/* ---------------- live ---------------- */
function durationSeconds() {
  const m = Number($('#inMin').value) || 0;
  const s = Number($('#inSec').value) || 0;
  return Math.max(0, Math.round(m * 60 + s));
}

function setDuration(sec) {
  $('#inMin').value = Math.floor(sec / 60);
  $('#inSec').value = sec % 60;
  $$('.chip[data-preset]').forEach(c => c.classList.toggle('on', Number(c.dataset.preset) === sec));
}

async function loadTargetPicker() {
  try {
    const { items } = await api('/api/targets');
    state.targets = items;
    const box = $('#liveTargetPick');
    const groups = {};
    items.forEach(t => (groups[t.grp || '其它'] ||= []).push(t));
    box.innerHTML = Object.entries(groups).map(([g, list]) => `
      <div style="margin-bottom:8px">
        <div style="font-size:11px;color:var(--dim);margin-bottom:5px">${g}</div>
        <div class="chips">${list.map(t =>
          `<span class="chip" data-tid="${t.id}">${t.name}</span>`).join('')}</div>
      </div>`).join('') +
      `<div class="row" style="margin-top:6px">
         <span class="chip" id="chipAll">全选</span>
         <span class="chip" id="chipNone">全不选</span>
       </div>`;
    $$('#liveTargetPick .chip[data-tid]').forEach(c => {
      c.onclick = () => c.classList.toggle('on');
    });
    const all = $('#chipAll'), none = $('#chipNone');
    if (all) all.onclick = () => $$('#liveTargetPick .chip[data-tid]').forEach(c => c.classList.add('on'));
    if (none) none.onclick = () => $$('#liveTargetPick .chip[data-tid]').forEach(c => c.classList.remove('on'));
    refreshAnalysisPickers();
  } catch (e) { toast(e.message, true); }
}

function selectedTargetIds() {
  return $$('#liveTargetPick .chip[data-tid].on').map(c => Number(c.dataset.tid));
}

async function startTest() {
  const btn = $('#btnStart');
  btn.disabled = true;
  try {
    const body = {
      duration: durationSeconds(),
      interval: Number($('#inInterval').value) || 5,
      timeout: Number($('#inTimeout').value) || 1,
      target_ids: selectedTargetIds().length ? selectedTargetIds() : null,
      note: ($('#inNote') ? $('#inNote').value.trim() : ''),
    };
    if (body.duration === 0) { toast('请填写大于 0 的测试时长', true); btn.disabled = false; return; }
    const s = await api('/api/session/start', { method: 'POST', body });
    toast(`测试已启动 #${s.session_id}`);
    beginPolling();
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
  }
}

async function stopTest() {
  try {
    await api('/api/session/stop', { method: 'POST' });
    toast('已停止');
    await pollLive();
  } catch (e) { toast(e.message, true); }
}

function beginPolling() {
  if (state.timer) clearInterval(state.timer);
  pollLive();
  state.timer = setInterval(pollLive, 1000);
}

async function pollLive() {
  let data;
  try { data = await api('/api/session/live'); }
  catch (e) { return; }
  state.live = data;
  renderLive(data);
  if (!data.running && state.timer) {
    // 结束后再抓一次就停
    clearInterval(state.timer);
    state.timer = null;
    $('#btnStop').disabled = true;
    $('#btnStart').disabled = false;
    refreshDbPill();
  }
}

function renderLive(d) {
  const running = d.running;
  $('#btnStop').disabled = !running;
  $('#btnStart').disabled = running;
  $('#statusPill').classList.toggle('live', running);
  $('#statusText').textContent = running
    ? `测试中 #${d.session_id}` : (d.session_id ? `已结束 #${d.session_id}` : '空闲');
  $('#liveRoundTag').textContent = `第 ${d.rounds_done}/${d.rounds_total || '∞'} 轮`;

  const total = d.plan_seconds || d.elapsed || 0;
  const pct = total ? Math.min(100, d.elapsed / total * 100) : 0;
  $('#liveBar').style.width = `${pct.toFixed(1)}%`;
  $('#liveProgress').textContent = `${fmtClock(d.elapsed)} / ${total ? fmtClock(total) : '∞'}`;

  $('#liveMeta').textContent = d.session_id
    ? `间隔 ${d.interval}s · 超时 ${d.timeout}s · 目标 ${d.targets.length} 个 · 最后更新 ${d.last_round_at ? fmtTime(d.last_round_at) : '—'}`
      + (d.error ? ` · 错误：${d.error}` : '')
    : '—';

  const stats = d.stats || [];
  const grid = $('#liveGrid');
  if (!stats.length) {
    grid.innerHTML = `<div class="empty">点击「开始测试」后，这里会实时显示各平台延迟</div>`;
    ['#kpiBest', '#kpiWorst', '#kpiLoss', '#kpiJitter'].forEach(s => $(s).textContent = '—');
    return;
  }
  const valid = stats.filter(s => s.avg !== null);
  const best = valid.length ? valid.reduce((a, b) => a.avg <= b.avg ? a : b) : null;
  const worst = valid.length ? valid.reduce((a, b) => a.avg >= b.avg ? a : b) : null;
  const totalSent = stats.reduce((n, s) => n + s.sent, 0);
  const totalRecv = stats.reduce((n, s) => n + s.recv, 0);
  const jitters = stats.filter(s => s.jitter !== null).map(s => s.jitter);

  $('#kpiBest').innerHTML = best ? `${fmtMs(best.avg)}<small>ms ${best.target}</small>` : '—';
  $('#kpiWorst').innerHTML = worst ? `${fmtMs(worst.avg)}<small>ms ${worst.target}</small>` : '—';
  $('#kpiLoss').innerHTML = totalSent ? `${fmtPct((totalSent - totalRecv) * 100 / totalSent)}`
                                      : '—';
  $('#kpiJitter').innerHTML = jitters.length
    ? `${fmtMs(jitters.reduce((a, b) => a + b, 0) / jitters.length)}<small>ms</small>` : '—';

  grid.innerHTML = stats.map((s, i) => {
    const color = COLORS[i % COLORS.length];
    return `<div class="tcard">
      <div class="top">
        <div style="min-width:0">
          <div class="name">${s.target}</div>
          <div class="host">${s.host}${s.proto === 'icmp' ? ' · icmp' : ''}</div>
        </div>
        ${badge(s.grade)}
      </div>
      <div class="ms ${gradeClass(s.grade)}">${fmtMs(s.avg)}<u>ms</u></div>
      <div class="sub">
        <span>最小 ${fmtMs(s.mn)}</span>
        <span>P95 ${fmtMs(s.p95)}</span>
        <span>抖动 ${fmtMs(s.jitter)}</span>
        <span>丢 ${fmtPct(s.loss)}</span>
      </div>
      ${sparkline(s.spark, color, 240, 34, s.spark_ts)}
    </div>`;
  }).join('');
}

function refreshDbPill() {
  api('/api/stats/overview').then(o => { $('#dbSamples').textContent = o.samples; }).catch(() => {});
}

/* ---------------- history ---------------- */
async function loadSessions() {
  const body = $('#sessionRows');
  try {
    const { items, total } = await api('/api/sessions?limit=60');
    state.sessions = items;
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="8" class="hint">还没有历史记录，先去「实时测试」跑一次</td></tr>`;
      return;
    }
    body.innerHTML = items.map(s => `<tr>
      <td>${s.id}</td>
      <td style="text-align:left">${fmtTime(s.started_at)}${s.note ? `<span class="tag">${s.note}</span>` : ''}</td>
      <td class="num">${s.duration ? fmtDur(s.duration) : '手动'}</td>
      <td class="num">${s.interval}s</td>
      <td class="num">${(s.target_ids || '').split(',').filter(Boolean).length}</td>
      <td class="num">${s.rounds_done}${s.rounds ? '/' + s.rounds : ''}</td>
      <td><span class="tag">${({ running: '进行中', done: '完成', stopped: '已停止', error: '出错' })[s.status] || s.status}</span></td>
      <td>
        <button class="btn sm ghost" data-view-session="${s.id}">查看</button>
        <button class="btn sm danger" data-del-session="${s.id}">删除</button>
      </td>
    </tr>`).join('');
    $$('[data-view-session]').forEach(b => b.onclick = () => showSession(Number(b.dataset.viewSession)));
    $$('[data-del-session]').forEach(b => b.onclick = () => deleteSession(Number(b.dataset.delSession)));
  } catch (e) {
    body.innerHTML = `<tr><td colspan="8" class="hint">${e.message}</td></tr>`;
  }
}

async function showSession(id) {
  const box = $('#sessionDetail');
  box.innerHTML = `<div class="hint">加载中…</div>`;
  try {
    const s = await api(`/api/sessions/${id}`);
    $('#detailTitle').textContent = `会话 #${s.id} · ${fmtTime(s.started_at)}`
      + (s.note ? ` · ${s.note}` : '');
    $('#detailActions').style.display = 'flex';
    $('#btnExportCsv').onclick = () => download(`/api/sessions/${id}/export.csv`);
    $('#btnExportJson').onclick = () => download(`/api/sessions/${id}/export.json`);
    $('#btnExportHtml').onclick = () => window.open(`/api/sessions/${id}/report.html`, '_blank');
    $('#btnLoadChart').onclick = () => {
      switchView('analysis');
      $('#anMode').value = 'session';
      $('#anSessionWrap').style.display = '';
      $('#anWindowWrap').style.display = 'none';
      $('#anSession').value = id;
      loadAnalysisSeries();
    };

    const rows = s.summary || [];
    if (!rows.length) {
      box.innerHTML = `<div class="empty">该会话没有样本</div>`;
      return;
    }
    box.innerHTML = `
      <div class="grid k4" style="margin-bottom:12px">
        <div class="kpi"><span class="v">${s.rounds_done}</span><span class="l">完成轮次</span></div>
        <div class="kpi"><span class="v">${rows.reduce((n, r) => n + r.sent, 0)}</span><span class="l">探测次数</span></div>
        <div class="kpi"><span class="v">${fmtPct(rows.reduce((n, r) => n + r.loss, 0) / rows.length)}</span><span class="l">平均丢包率</span></div>
        <div class="kpi"><span class="v">${fmtDur(s.duration)}</span><span class="l">计划时长</span></div>
      </div>
      <div class="scroll">
        <table>
          <thead><tr>
            <th style="text-align:left">目标</th><th style="text-align:left">地址</th>
            <th>次数</th><th>丢包</th><th>最小</th><th>平均</th><th>最大</th><th>在线率</th>
          </tr></thead>
          <tbody>${rows.map(r => `<tr>
            <td>${r.target}</td>
            <td style="text-align:left" class="mono" style="color:var(--dim)">${r.host}</td>
            <td class="num">${r.sent}</td>
            <td class="num">${fmtPct(r.loss)}</td>
            <td class="num">${fmtMs(r.mn)}</td>
            <td class="num">${fmtMs(r.avg)}</td>
            <td class="num">${fmtMs(r.mx)}</td>
            <td class="num">${fmtPct(r.ok_rate)}</td>
          </tr>`).join('')}</tbody>
        </table>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${e.message}</div>`;
  }
}

async function deleteSession(id) {
  if (!confirm(`删除会话 #${id} 及其所有样本？`)) return;
  try {
    await api(`/api/sessions/${id}`, { method: 'DELETE' });
    toast(`已删除 #${id}`);
    loadSessions();
    $('#sessionDetail').innerHTML = `<div class="empty">选择上方任一会话查看汇总</div>`;
    $('#detailActions').style.display = 'none';
    refreshDbPill();
  } catch (e) { toast(e.message, true); }
}

function download(url) {
  const a = document.createElement('a');
  a.href = url;
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/* ---------------- analysis ---------------- */
async function loadAnalysisSessions() {
  try {
    const { items } = await api('/api/sessions?limit=100');
    const sel = $('#anSession');
    sel.innerHTML = items.map(s =>
      `<option value="${s.id}">#${s.id} · ${fmtTime(s.started_at)}${s.note ? ' · ' + s.note : ''} · ${s.rounds_done}轮</option>`).join('')
      || `<option value="">（无历史）</option>`;
    if (items.length) loadAnalysisSeries();
  } catch (e) { toast(e.message, true); }
}

function analysisMode() {
  return $('#anMode') ? $('#anMode').value : 'session';
}

function selectedAnalyticTargets() {
  return $$('#anTargetChips .chip.on').map(c => Number(c.dataset.tid)).filter(Boolean);
}

async function loadAnalysisSeries() {
  if (analysisMode() === 'window') return loadAnalysisWindow();
  const sid = $('#anSession').value;
  if (!sid) return;
  try {
    const data = await api(`/api/sessions/${sid}/series?buckets=90`);
    state.chartSeries = data.series || [];
    renderAnalysisChips();
    loadComparisonPickers();
    drawChart();
    renderTrend();
    renderSpikes();
  } catch (e) { toast(e.message, true); }
}

async function loadAnalysisWindow() {
  const hours = Number($('#anWindow').value) || 24;
  const ids = selectedAnalyticTargets();
  const end = new Date();
  const start = new Date(end.getTime() - hours * 3600 * 1000);
  const iso = d => d.toISOString().slice(0, 19);
  try {
    const data = await api(`/api/analytics/window?start=${iso(start)}&end=${iso(end)}`
      + (ids.length ? `&target_ids=${ids.join(',')}` : ''));
    state.chartSeries = data.series || [];
    renderAnalysisChips();
    loadComparisonPickers();
    drawChart();
    renderTrend();
    renderSpikes();
    if (!state.chartSeries.length) toast('所选时间范围内没有样本');
  } catch (e) { toast(e.message, true); }
}

function renderAnalysisChips() {
  const box = $('#anTargetChips');
  if (!box) return;
  const prev = new Set(selectedAnalyticTargets());
  const series = state.chartSeries || [];
  box.innerHTML = series.length
    ? series.map(s => `<span class="chip${prev.size === 0 || prev.has(s.target_id) ? ' on' : ''}" data-tid="${s.target_id}">${s.target}</span>`).join('')
      + `<span class="chip" id="anChipAll">全选</span><span class="chip" id="anChipNone">全不选</span>`
    : '';
  $$('#anTargetChips .chip[data-tid]').forEach(c => {
    c.onclick = () => { c.classList.toggle('on'); drawChart(); refreshTrendFromChart(); };
  });
  const all = $('#anChipAll'), none = $('#anChipNone');
  if (all) all.onclick = () => { $$('#anTargetChips .chip[data-tid]').forEach(c => c.classList.add('on')); drawChart(); refreshTrendFromChart(); };
  if (none) none.onclick = () => { $$('#anTargetChips .chip[data-tid]').forEach(c => c.classList.remove('on')); drawChart(); refreshTrendFromChart(); };
}

function loadComparisonPickers() {
  const cmpA = $('#cmpA'), cmpB = $('#cmpB');
  if (!cmpA || !cmpB) return;
  const opts = (state.chartSeries || []).map(s => `<option value="${s.target_id}">${s.target}</option>`).join('');
  cmpA.innerHTML = opts; cmpB.innerHTML = opts;
  if ((state.chartSeries || []).length > 1) cmpB.selectedIndex = 1;
}

function visibleSeries() {
  const picked = selectedAnalyticTargets();
  const series = state.chartSeries || [];
  return picked.length ? series.filter(s => picked.includes(s.target_id)) : series;
}

function refreshTrendFromChart() { renderTrend(); }

function drawChart() {
  const svg = $('#chartSvg');
  const series = visibleSeries();
  const tip = $('#chartTip');
  if (tip) { tip.hidden = true; tip.innerHTML = ''; }
  if (!series.length) {
    svg.innerHTML = `<text x="50%" y="50%" text-anchor="middle">没有可绘制的数据</text>`;
    $('#chartLegend').innerHTML = '';
    return;
  }
  const W = 1000, H = 260, PAD = { l: 46, r: 14, t: 14, b: 26 };
  // viewBox 必须显式设置，否则 1000×260 的坐标会被按 CSS 像素拉伸错位
  svg.setAttribute('viewBox', `0 0 ${W} ${H}`);
  svg.setAttribute('preserveAspectRatio', 'none');
  const n = Math.max(...series.map(s => s.points.length), 2);
  const allVals = series.flatMap(s => s.points.map(p => p.avg).filter(v => v !== null));
  if (!allVals.length) {
    svg.innerHTML = `<text x="50%" y="50%" text-anchor="middle">全部样本超时</text>`;
    return;
  }
  let maxV = Math.max(...allVals) * 1.15;
  maxV = Math.max(maxV, 20);
  const x = i => PAD.l + (W - PAD.l - PAD.r) * (n <= 1 ? 0 : i / (n - 1));
  const y = v => H - PAD.b - (H - PAD.t - PAD.b) * (v / maxV);

  const grid = [];
  const ticks = 5;
  for (let i = 0; i <= ticks; i++) {
    const v = maxV * i / ticks;
    grid.push(`<line class="grid-line" x1="${PAD.l}" y1="${y(v)}" x2="${W - PAD.r}" y2="${y(v)}"/>`);
    grid.push(`<text x="${PAD.l - 8}" y="${y(v) + 3.5}" text-anchor="end">${v.toFixed(0)}</text>`);
  }
  const paths = series.map((s, idx) => {
    const color = COLORS[idx % COLORS.length];
    // 空桶直接跳过但不断线：稀疏采样也能看到趋势，而不是只剩孤立点
    const live = [];
    s.points.forEach((p, i) => { if (p.avg !== null) live.push([x(i), y(p.avg)]); });
    let d = '';
    if (live.length === 1) {
      const [cx, cy] = live[0];
      d = `M${cx.toFixed(1)},${cy.toFixed(1)} l0.01,0`;   // 单点画个微小线段才可见
    } else if (live.length > 1) {
      d = live.map(([cx, cy], i) => `${i ? 'L' : 'M'}${cx.toFixed(1)},${cy.toFixed(1)}`).join(' ');
    }
    const dots = s.points.map((p, i) => p.loss > 0
      ? `<circle cx="${x(i).toFixed(1)}" cy="${(H - PAD.b - 2).toFixed(1)}" r="2.4" fill="#ef5a6f"/>` : '').join('');
    const single = live.length === 1
      ? `<circle cx="${live[0][0].toFixed(1)}" cy="${live[0][1].toFixed(1)}" r="2.6" fill="${color}"/>` : '';
    return `<path d="${d}" fill="none" stroke="${color}" stroke-width="1.8"
      stroke-linejoin="round" stroke-linecap="round" vector-effect="non-scaling-stroke"/>${single}${dots}`;
  }).join('');

  const first = series[0].points[0]?.t;
  const last = series[0].points[series[0].points.length - 1]?.t;
  const label = v => {
    if (!v) return '';
    const d = typeof v === 'number' ? new Date(v * 1000) : new Date(v);
    return `${pad2(d.getMonth() + 1)}/${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
  };
  svg.innerHTML = `
    <line class="axis" x1="${PAD.l}" y1="${H - PAD.b}" x2="${W - PAD.r}" y2="${H - PAD.b}"/>
    ${grid.join('')}
    ${paths}
    <text x="${PAD.l}" y="${H - 8}">${label(first)}</text>
    <text x="${W - PAD.r}" y="${H - 8}" text-anchor="end">${label(last)}</text>
    <text x="${PAD.l - 34}" y="${PAD.t + 4}">ms</text>
    <g id="hoverLayer" style="display:none">
      <line class="hover-line" id="hoverLine" x1="0" y1="${PAD.t}" x2="0" y2="${H - PAD.b}"/>
      <g id="hoverDots"></g>
    </g>`;
  $('#chartLegend').innerHTML = series.map((s, i) =>
    `<span><i style="background:${COLORS[i % COLORS.length]}"></i>${s.target}</span>`).join('') +
    `<span><i style="background:#ef5a6f;height:5px;width:5px;border-radius:50%"></i>丢包点</span>`;

  bindChartHover({ series, svg, x, y, n, W, H, PAD });
}

/* ---- 悬停读数：十字线 + 该时刻每条线的真实采样值 ---- */
function bindChartHover(ctx) {
  const { series, svg, x, y, n, H, PAD } = ctx;
  const wrap = $('#chartWrap');
  const tip = $('#chartTip');
  const layer = svg.querySelector('#hoverLayer');
  const line = svg.querySelector('#hoverLine');
  const dots = svg.querySelector('#hoverDots');
  if (!wrap || !tip || !layer || !line || !dots) return;

  const nearestIndex = (clientX) => {
    const rect = svg.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    const vx = ratio * 1000;                 // 换回 viewBox 坐标
    const i = Math.round((vx - PAD.l) / ((1000 - PAD.l - PAD.r) / Math.max(1, n - 1)));
    return Math.min(n - 1, Math.max(0, i));
  };

  const pickSample = (points, i) => {
    // 桶内有多条原始读数时，取离该时刻最近的那条
    const bucket = points[i];
    if (!bucket || !bucket.samples || !bucket.samples.length) {
      return bucket && bucket.avg !== null ? { ms: bucket.avg, t: null } : { ms: null, t: null };
    }
    const want = series[0].points[i]?.t ?? 0;
    let best = bucket.samples[0], bestD = Infinity;
    for (const s of bucket.samples) {
      const ts = s.t ? new Date(s.t).getTime() / 1000 : 0;
      const d = Math.abs(ts - want);
      if (d < bestD) { bestD = d; best = s; }
    }
    return best;
  };

  const show = (ev) => {
    const i = nearestIndex(ev.clientX);
    const cx = x(i);
    layer.style.display = '';
    line.setAttribute('x1', cx);
    line.setAttribute('x2', cx);
    dots.innerHTML = '';
    const rows = [];
    let stamp = null;

    series.forEach((s, idx) => {
      const color = COLORS[idx % COLORS.length];
      const bucket = s.points[i];
      const sample = pickSample(s.points, i);
      if (!stamp) {
        stamp = (sample && sample.t) || (bucket && bucket.t
          ? new Date(bucket.t * 1000).toISOString() : null);
      }
      if (!bucket || (bucket.avg === null && !(bucket.samples || []).length)) return;
      const ms = sample ? sample.ms : null;
      if (ms !== null && ms !== undefined) {
        dots.insertAdjacentHTML('beforeend',
          `<circle class="hover-dot" cx="${cx.toFixed(1)}" cy="${y(ms).toFixed(1)}"
             r="3.4" fill="${color}"/>`);
      }
      const loss = bucket.loss || 0;
      const raw = (bucket.samples || []).length;
      rows.push({
        name: s.target, color, ms,
        loss, raw,
        avg: bucket.avg,                       // 同桶多次采样时用于补显均值
      });
    });

    if (!rows.length) { tip.hidden = true; return; }
    const timeText = stamp ? fmtTime(stamp) : '—';
    const body = rows.map(r => {
      const hasMs = r.ms !== null && r.ms !== undefined;
      const val = hasMs
        ? `${fmtMs(r.ms)}<span class="tip-sub">ms</span>`
        : '<span class="tip-loss">超时</span>';
      // 同桶多次采样时补一句均值，丢包也标出来
      const notes = [];
      if (r.raw > 1) notes.push(`${r.raw} 次均 ${fmtMs(r.avg)}`);
      if (r.loss > 0 && hasMs) notes.push(`丢 ${fmtPct(r.loss)}`);
      return `<div class="tip-row">
          <span class="tip-name"><i style="background:${r.color}"></i>${r.name}</span>
          <span class="tip-val">${val}</span>
        </div>`
        + (notes.length ? `<div class="tip-raw">${notes.join(' · ')}</div>` : '');
    }).join('');
    tip.innerHTML = `<div class="tip-time">${timeText}</div>${body}`;
    tip.hidden = false;

    // 定位：优先放在十字线右侧，放不下就翻到左侧；竖直方向置顶对齐，
    // 这样提示框不会压住光标附近正在读的那段曲线。
    const wrapRect = wrap.getBoundingClientRect();
    const rect = svg.getBoundingClientRect();
    const px = rect.left - wrapRect.left + (cx / 1000) * rect.width;
    tip.style.left = '0px';
    tip.style.top = '0px';
    const tw = tip.offsetWidth;
    let left = px + 18;
    if (left + tw > wrapRect.width - 6) left = px - tw - 18;
    tip.style.left = `${Math.max(6, left)}px`;
    tip.style.top = '6px';
  };

  const hide = () => { layer.style.display = 'none'; tip.hidden = true; };

  svg.addEventListener('mousemove', show);
  svg.addEventListener('mouseleave', hide);
  // 触屏：按住并滑动也能读数
  svg.addEventListener('touchstart', (e) => { if (e.touches[0]) show(e.touches[0]); }, { passive: true });
  svg.addEventListener('touchmove', (e) => { if (e.touches[0]) show(e.touches[0]); }, { passive: true });
  svg.addEventListener('touchend', hide);
}

const TREND_LABEL = { up: ['↗ 变慢', 'g-C'], down: ['↘ 变快', 'g-A'], flat: ['→ 平稳', ''] };

function renderTrend() {
  const body = $('#trendRows');
  const series = visibleSeries();
  if (!series.length) {
    body.innerHTML = `<tr><td colspan="7" class="hint">选择会话后生成</td></tr>`;
    return;
  }
  body.innerHTML = series.map(s => {
    const pts = s.points.filter(p => p.avg !== null).map(p => p.avg);
    const avg = pts.length ? pts.reduce((a, b) => a + b, 0) / pts.length : null;
    const loss = s.points.length ? s.points.reduce((a, p) => a + p.loss, 0) / s.points.length : 0;
    const tr = s.trend || {};
    const [txt, cls] = TREND_LABEL[tr.direction] || TREND_LABEL.flat;
    const grade = avg === null ? 'X' : (avg <= 40 ? 'S' : avg <= 70 ? 'A' : avg <= 110 ? 'B' : 'C');
    return `<tr>
      <td>${s.target}</td>
      <td class="num">${fmtMs(avg)}</td>
      <td class="num">${fmtMs(pts.length ? percentile(pts, .95) : null)}</td>
      <td class="num">${fmtMs(std(pts))}</td>
      <td class="num">${fmtPct(loss)}</td>
      <td class="${cls}">${txt}${tr.delta !== null && tr.delta !== undefined ? ` ${tr.delta > 0 ? '+' : ''}${fmtMs(tr.delta)}` : ''}</td>
      <td>${badge(grade)}</td>
    </tr>`;
  }).join('');
}

function percentile(arr, q) {
  const a = [...arr].sort((x, y) => x - y);
  return a[Math.min(a.length - 1, Math.max(0, Math.round(q * (a.length - 1))))];
}
function std(arr) {
  if (arr.length < 2) return 0;
  const m = arr.reduce((a, b) => a + b, 0) / arr.length;
  return Math.sqrt(arr.reduce((a, b) => a + (b - m) ** 2, 0) / arr.length);
}

function renderSpikes() {
  const rows = [];
  visibleSeries().forEach(s => (s.spikes || []).slice(0, 6).forEach(k => rows.push({ target: s.target, ...k })));
  const body = $('#spikeRows');
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="4" class="hint">未检测到明显抖动异常</td></tr>`;
    return;
  }
  rows.sort((a, b) => (b.over || 0) - (a.over || 0));
  body.innerHTML = rows.slice(0, 40).map(r => `<tr>
    <td>${r.target}</td>
    <td style="text-align:left">${fmtTime(r.t)}</td>
    <td class="num g-C">${fmtMs(r.ms)}</td>
    <td class="num">+${fmtMs(r.over)}</td>
  </tr>`).join('');
}

async function compareTargets() {
  const a = Number($('#cmpA').value), b = Number($('#cmpB').value);
  if (!a || !b) { toast('请选择两个目标', true); return; }
  if (a === b) { toast('请选择两个不同的目标', true); return; }
  const box = $('#cmpResult');
  box.innerHTML = `<div class="hint">对比中…</div>`;

  let A, B, C;
  try {
    if (analysisMode() === 'window') {
      // 时间窗模式没有会话，就地从前端已加载的序列算统计
      const pick = id => (state.chartSeries || []).find(s => s.target_id === id);
      const sa = pick(a), sb = pick(b);
      if (!sa || !sb) { box.innerHTML = `<div class="empty">当前视图中没有这两个目标的数据</div>`; return; }
      const statOf = s => {
        const vals = s.points.filter(p => p.avg !== null).map(p => p.avg);
        const losses = s.points.map(p => p.loss || 0);
        if (!vals.length) return { target: s.target, avg: null, mn: null, mx: null,
                                   p95: null, jitter: null, loss: 100, grade: 'X' };
        const avg = vals.reduce((x, y) => x + y, 0) / vals.length;
        const loss = losses.reduce((x, y) => x + y, 0) / (losses.length || 1);
        const jitter = std(vals);
        const grade = avg <= 40 ? 'S' : avg <= 70 ? 'A' : avg <= 110 ? 'B' : 'C';
        return { target: s.target, avg, mn: Math.min(...vals), mx: Math.max(...vals),
                 p95: percentile(vals, .95), jitter, loss, grade };
      };
      A = statOf(sa); B = statOf(sb);
      const score = x => x.avg === null ? 1e9 : x.avg + x.loss * 10 + (x.jitter || 0) * 0.5;
      C = { winner: score(A) < score(B) ? A.target : score(B) < score(A) ? B.target : 'tie',
            delta_avg: A.avg !== null && B.avg !== null ? A.avg - B.avg : null };
    } else {
      const sid = $('#anSession').value;
      if (!sid) { toast('请先选择会话', true); return; }
      const data = await api(`/api/sessions/${sid}/compare?a=${a}&b=${b}`);
      A = data.a; B = data.b; C = data.compare;
    }
  } catch (e) {
    box.innerHTML = `<div class="empty">${e.message}</div>`;
    return;
  }

    const line = (k, fn) => `<tr><td style="text-align:left">${k}</td>
      <td class="num">${fn(A)}</td><td class="num">${fn(B)}</td></tr>`;
    // 评分按各自指标现算，避免把同一侧的分数渲染两遍
    const scoreOf = x => (x.avg === null || x.avg === undefined)
      ? null : x.avg + x.loss * 10 + (x.jitter || 0) * 0.5;
    const winnerCard = (x) => {
      const win = C.winner === x.target;
      return `<div class="card" style="padding:14px;border-color:${win ? 'rgba(53,208,186,.5)' : 'var(--line)'}">
        <div class="row" style="justify-content:space-between">
          <b>${x.target}</b>${badge(x.grade)}
        </div>
        <div class="kpi" style="margin-top:6px">
          <span class="v ${gradeClass(x.grade)}">${fmtMs(x.avg)}<small>ms</small></span>
          <span class="l">平均延迟 · 丢包 ${fmtPct(x.loss)} · 抖动 ${fmtMs(x.jitter)}</span>
        </div>
        ${win ? '<div class="tag" style="color:var(--accent);border-color:rgba(53,208,186,.4)">本组更优</div>' : ''}
      </div>`;
    };
    box.innerHTML = `
      <div class="grid k2" style="margin-bottom:12px">${winnerCard(A)}${winnerCard(B)}</div>
      <table>
        <thead><tr><th style="text-align:left">指标</th><th>${A.target}</th><th>${B.target}</th></tr></thead>
        <tbody>
          ${line('平均延迟 (ms)', x => fmtMs(x.avg))}
          ${line('最小 / 最大', x => `${fmtMs(x.mn)} / ${fmtMs(x.mx)}`)}
          ${line('P95 (ms)', x => fmtMs(x.p95))}
          ${line('抖动 (ms)', x => fmtMs(x.jitter))}
          ${line('丢包率', x => fmtPct(x.loss))}
          ${line('综合评分 (越低越好)', x => scoreOf(x) === null ? '—' : fmtMs(scoreOf(x)))}
        </tbody>
      </table>
      <div class="hint" style="margin-top:10px">
        评分 = 平均延迟 + 丢包×10 + 抖动×0.5，用于综合判断稳定性。
        ${C.delta_avg !== null && C.delta_avg !== undefined
          ? `\n平均延迟差 ${C.delta_avg > 0 ? '+' : ''}${fmtMs(C.delta_avg)} ms（A 相对 B）。` : ''}
      </div>`;
}

function refreshAnalysisPickers() { /* 目标变化后无需特殊处理 */ }

/* ---------------- heatmap ---------------- */
function lossColor(loss, sent) {
  if (!sent) return 'rgba(255,255,255,.03)';
  if (loss >= 50) return 'rgba(239,90,111,.85)';
  if (loss >= 20) return 'rgba(240,138,60,.8)';
  if (loss >= 5) return 'rgba(232,195,65,.75)';
  if (loss > 0) return 'rgba(82,192,122,.6)';
  return 'rgba(45,212,167,.45)';
}

async function loadHeatmap() {
  const box = $('#heatmap');
  const bucket = $('#heatBucket').value;
  const days = Number($('#heatDays').value) || 14;
  box.innerHTML = `<div class="hint">加载中…</div>`;
  try {
    const data = await api(`/api/analytics/heatmap?days=${days}&bucket=${bucket}`);
    if (!data.targets.length) {
      box.innerHTML = `<div class="empty">该时间段内没有样本</div>`;
      return;
    }
    const head = data.labels.map(l => `<th style="text-align:center;min-width:26px">${l}</th>`).join('');
    const rows = data.targets.map(t => {
      const cells = t.cells.map((c, i) => {
        if (!c) return `<td style="padding:2px"><div style="height:20px;border-radius:3px;background:rgba(255,255,255,.03)" title="无数据"></div></td>`;
        const tip = `${t.target} · ${data.labels[i]} · 丢包 ${c.loss}% · 平均 ${c.avg ?? '—'}ms · ${c.sent} 次`;
        return `<td style="padding:2px"><div title="${tip}" style="height:20px;border-radius:3px;background:${lossColor(c.loss, c.sent)}"></div></td>`;
      }).join('');
      return `<tr><td style="text-align:left;white-space:nowrap">${t.target}</td>${cells}</tr>`;
    }).join('');
    box.innerHTML = `<div class="scroll" style="max-height:none"><table style="min-width:${data.slots * 30 + 130}px">
      <thead><tr><th style="text-align:left">目标</th>${head}</tr></thead>
      <tbody>${rows}</tbody></table></div>
      <div class="legend">
        <span><i style="background:rgba(45,212,167,.45);height:10px;width:14px"></i>无丢包</span>
        <span><i style="background:rgba(82,192,122,.6);height:10px;width:14px"></i>&lt;5%</span>
        <span><i style="background:rgba(232,195,65,.75);height:10px;width:14px"></i>5-20%</span>
        <span><i style="background:rgba(240,138,60,.8);height:10px;width:14px"></i>20-50%</span>
        <span><i style="background:rgba(239,90,111,.85);height:10px;width:14px"></i>≥50%</span>
      </div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${e.message}</div>`;
  }
}

/* ---------------- long-term trend ---------------- */
const DIR_LABEL = { up: ['↗ 变慢', 'g-C'], down: ['↘ 变快', 'g-A'], flat: ['→ 平稳', ''] };

async function loadLongTrend() {
  const days = Number($('#trendDays').value) || 30;
  const body = $('#longTrendRows');
  body.innerHTML = `<tr><td colspan="6" class="hint">分析中…</td></tr>`;
  try {
    const data = await api(`/api/analytics/trends?days=${days}`);
    if (!data.series.length) {
      body.innerHTML = `<tr><td colspan="6" class="hint">近 ${days} 天没有数据</td></tr>`;
      return;
    }
    body.innerHTML = data.series.map(s => {
      const [txt, cls] = DIR_LABEL[s.direction] || DIR_LABEL.flat;
      return `<tr>
        <td>${s.target}</td>
        <td class="num">${s.days_count}</td>
        <td class="num">${fmtMs(s.avg_overall)}</td>
        <td class="num">${s.delta === null ? '—' : (s.delta > 0 ? '+' : '') + fmtMs(s.delta)}</td>
        <td class="num">${s.delta_pct === null ? '—' : (s.delta_pct > 0 ? '+' : '') + s.delta_pct.toFixed(1) + '%'}</td>
        <td class="${cls}">${txt}</td>
      </tr>`;
    }).join('');
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="hint">${e.message}</td></tr>`;
  }
}

/* ---------------- schedules ---------------- */
async function loadSchedules() {
  const body = $('#schedRows');
  try {
    const data = await api('/api/schedules');
    const pill = $('#schedState');
    pill.classList.toggle('live', data.running);
    pill.querySelector('span:last-child').textContent = data.running ? '调度器运行中' : '调度器停止';

    const items = data.items || [];
    if (!items.length) {
      body.innerHTML = `<tr><td colspan="9" class="hint">还没有计划，点右上角「新建计划」</td></tr>`;
    } else {
      body.innerHTML = items.map(s => `<tr>
        <td>${s.id}</td>
        <td style="text-align:left">${s.name}</td>
        <td class="num mono">${s.at_time}</td>
        <td class="num">${fmtDur(s.duration)}</td>
        <td class="num">${s.interval}s</td>
        <td class="num">${(s.target_ids || '').split(',').filter(Boolean).length || '全部'}</td>
        <td class="num">${s.last_run_at ? fmtTime(s.last_run_at) : '—'}</td>
        <td>${s.enabled ? '<span class="g-A">● 启用</span>' : '<span class="hint">○ 停用</span>'}</td>
        <td>
          <button class="btn sm ghost" data-run-sched="${s.id}">立即运行</button>
          <button class="btn sm ghost" data-toggle-sched="${s.id}">${s.enabled ? '停用' : '启用'}</button>
          <button class="btn sm danger" data-del-sched="${s.id}">删除</button>
        </td>
      </tr>`).join('');
      $$('[data-run-sched]').forEach(b => b.onclick = () => runSchedule(Number(b.dataset.runSched)));
      $$('[data-toggle-sched]').forEach(b => b.onclick = () => toggleSchedule(Number(b.dataset.toggleSched), items));
      $$('[data-del-sched]').forEach(b => b.onclick = () => deleteSchedule(Number(b.dataset.delSched)));
    }
    if (data.fired && data.fired.length) {
      const last = data.fired[0];
      $('#schedHint').textContent =
        `最近一次自动触发：${last.name} @ ${fmtTime(last.at)} → 会话 #${last.session_id}`;
    } else {
      $('#schedHint').textContent =
        '计划到点后自动开测，错过超过 30 分钟不再补跑（避免开机时连跑多次）。';
    }
  } catch (e) {
    body.innerHTML = `<tr><td colspan="9" class="hint">${e.message}</td></tr>`;
  }
  loadAlerts();
}

async function runSchedule(id) {
  try {
    const s = await api(`/api/schedules/${id}/run`, { method: 'POST' });
    toast(`已启动会话 #${s.session_id}`);
    beginPolling();
    switchView('live');
  } catch (e) { toast(e.message, true); }
}

async function toggleSchedule(id, items) {
  const s = items.find(x => x.id === id);
  try {
    await api(`/api/schedules/${id}`, { method: 'PATCH', body: { enabled: s.enabled ? 0 : 1 } });
    loadSchedules();
  } catch (e) { toast(e.message, true); }
}

async function deleteSchedule(id) {
  if (!confirm('删除该计划？')) return;
  try { await api(`/api/schedules/${id}`, { method: 'DELETE' }); loadSchedules(); }
  catch (e) { toast(e.message, true); }
}

function openScheduleDialog() {
  $('#formSched').reset();
  $('#sTime').value = '08:30';
  $('#sMinutes').value = 5;
  $('#sInterval').value = 5;
  $('#sTimeout').value = 1;
  const box = $('#sTargets');
  box.innerHTML = state.targets.map(t =>
    `<span class="chip" data-sid="${t.id}">${t.name}</span>`).join('');
  $$('#sTargets .chip').forEach(c => c.onclick = () => c.classList.toggle('on'));
  $('#dlgSched').showModal();
}

async function submitSchedule(ev) {
  ev.preventDefault();
  const body = {
    name: $('#sName').value.trim(),
    at_time: $('#sTime').value.trim(),
    duration: Math.round((Number($('#sMinutes').value) || 5) * 60),
    interval: Number($('#sInterval').value) || 5,
    timeout: Number($('#sTimeout').value) || 1,
    target_ids: $$('#sTargets .chip.on').map(c => Number(c.dataset.sid)),
  };
  if (!body.name) { toast('请填写名称', true); return; }
  if (!/^\d{1,2}:\d{2}$/.test(body.at_time)) { toast('时刻格式应为 HH:MM', true); return; }
  try {
    await api('/api/schedules', { method: 'POST', body });
    $('#dlgSched').close();
    toast(`已创建计划 ${body.name}`);
    loadSchedules();
  } catch (e) { toast(e.message, true); }
}

/* ---------------- alerts ---------------- */
async function loadAlerts() {
  try {
    const data = await api('/api/alerts?limit=80');
    const unseen = data.unseen || 0;
    $('#alertCount').textContent = unseen;
    $('#alertBadge').textContent = `${unseen} 未读`;
    const th = data.thresholds || {};
    $('#alertThresholds').textContent =
      `判定阈值：平均延迟 > ${th.latency}ms、丢包 > ${th.loss}%、抖动 > ${th.jitter}ms（可在 data/config.json 的 thresholds 调整）`;
    const items = data.items || [];
    $('#alertRows').innerHTML = items.length ? items.map(a => `<tr${a.seen ? ' style="opacity:.55"' : ''}>
      <td style="text-align:left">${fmtTime(a.created_at)}</td>
      <td style="text-align:left">${a.target}</td>
      <td>${({ latency: '延迟', loss: '丢包', jitter: '抖动' })[a.kind] || a.kind}</td>
      <td class="num g-C">${fmtMs(a.value)}</td>
      <td class="num">${fmtMs(a.threshold)}</td>
      <td style="text-align:left">${a.message}</td>
    </tr>`).join('') : `<tr><td colspan="6" class="hint">暂无告警</td></tr>`;
  } catch (e) { /* 告警面板失败不影响主流程 */ }
}

async function markAlertsSeen() {
  try {
    const r = await api('/api/alerts/seen', { method: 'POST' });
    toast(`已标记 ${r.updated} 条为已读`);
    loadAlerts();
  } catch (e) { toast(e.message, true); }
}

/* ---------------- note / link comparison ---------------- */
async function loadNotes() {
  try {
    const { items } = await api('/api/analytics/notes');
    const selA = $('#noteA'), selB = $('#noteB');
    if (!selA || !selB) return;
    const opts = items.map(n =>
      `<option value="${n.note}">${n.note}（${n.sessions} 次 / ${n.samples} 样本）</option>`).join('');
    selA.innerHTML = opts; selB.innerHTML = opts;
    if (items.length > 1) selB.selectedIndex = 1;
  } catch (e) { /* 没有备注时静默 */ }
}

async function compareNotes() {
  const a = $('#noteA').value, b = $('#noteB').value;
  const box = $('#noteCompare');
  if (!a || !b) { toast('先给会话填备注再对比', true); return; }
  if (a === b) { toast('请选择两条不同的链路', true); return; }
  box.innerHTML = `<div class="hint">对比中…</div>`;
  try {
    const data = await api(`/api/analytics/notes/compare?a=${encodeURIComponent(a)}`
      + `&b=${encodeURIComponent(b)}`);
    const rows = data.rows || [];
    if (!rows.length) { box.innerHTML = `<div class="empty">没有可对比的数据</div>`; return; }
    const nicer = r => {
      if (r.a_avg === null || r.b_avg === null) return '<span class="hint">数据不全</span>';
      const d = r.b_avg - r.a_avg;
      const cls = Math.abs(d) < 5 ? '' : (d > 0 ? 'g-C' : 'g-A');
      return `<span class="${cls}">${d > 0 ? '+' : ''}${fmtMs(d)} ms</span>`;
    };
    box.innerHTML = `<div class="scroll"><table>
      <thead><tr><th style="text-align:left">目标</th><th>${a} 平均</th><th>丢包</th>
        <th>${b} 平均</th><th>丢包</th><th>B 相对 A</th></tr></thead>
      <tbody>${rows.map(r => `<tr>
        <td>${r.target}</td>
        <td class="num">${fmtMs(r.a_avg)}</td><td class="num">${fmtPct(r.a_loss)}</td>
        <td class="num">${fmtMs(r.b_avg)}</td><td class="num">${fmtPct(r.b_loss)}</td>
        <td class="num">${nicer(r)}</td>
      </tr>`).join('')}</tbody></table></div>`;
  } catch (e) {
    box.innerHTML = `<div class="empty">${e.message}</div>`;
  }
}

/* ---------------- import ---------------- */
async function importCsv(file) {
  const text = await file.text();
  try {
    const r = await api('/api/sessions/import.csv', {
      method: 'POST', body: text,
      headers: { 'Content-Type': 'text/csv' },
    });
    $('#importResult').innerHTML =
      `导入成功：${r.imported} 条样本 → 会话 #${r.session_id}`
      + (r.created_targets && r.created_targets.length
        ? `（新建目标：${r.created_targets.join('、')}）` : '');
    toast('导入完成');
    refreshDbPill();
  } catch (e) {
    $('#importResult').textContent = `导入失败：${e.message}`;
  }
}

/* ---------------- targets ---------------- */
async function loadTargets() {
  const body = $('#targetRows');
  try {
    const { items } = await api('/api/targets?all=true');
    body.innerHTML = items.map(t => `<tr>
      <td>${t.id}</td>
      <td style="text-align:left">${t.name}</td>
      <td style="text-align:left" class="mono">${t.host}</td>
      <td class="num">${t.proto === 'icmp' ? '—' : t.port}</td>
      <td>${t.proto.toUpperCase()}</td>
      <td style="text-align:left">${t.grp || '—'}</td>
      <td class="mono" style="color:var(--dim)">${t.ip || '—'}</td>
      <td>${t.enabled ? '<span class="g-A">● 启用</span>' : '<span class="hint">○ 停用</span>'}</td>
      <td>
        <button class="btn sm ghost" data-probe="${t.id}">测试</button>
        <button class="btn sm ghost" data-toggle="${t.id}">${t.enabled ? '停用' : '启用'}</button>
        <button class="btn sm danger" data-del="${t.id}">删除</button>
      </td>
    </tr>`).join('');
    $$('[data-probe]').forEach(b => b.onclick = async () => {
      b.disabled = true; b.textContent = '…';
      try {
        const r = await api(`/api/targets/${b.dataset.probe}/probe`, { method: 'POST' });
        b.textContent = r.ok ? `${fmtMs(r.ms)}ms` : '超时';
        b.classList.toggle('danger', !r.ok);
      } catch (e) { b.textContent = '失败'; }
      setTimeout(() => { b.disabled = false; b.textContent = '测试'; b.classList.remove('danger'); }, 2500);
    });
    $$('[data-toggle]').forEach(b => b.onclick = async () => {
      const t = items.find(x => x.id === Number(b.dataset.toggle));
      try {
        await api(`/api/targets/${t.id}`, { method: 'PATCH', body: { enabled: t.enabled ? 0 : 1 } });
        loadTargets();
      } catch (e) { toast(e.message, true); }
    });
    $$('[data-del]').forEach(b => b.onclick = async () => {
      if (!confirm('删除该目标？其历史样本也会一并删除。')) return;
      try { await api(`/api/targets/${b.dataset.del}`, { method: 'DELETE' }); loadTargets(); loadTargetPicker(); }
      catch (e) { toast(e.message, true); }
    });
  } catch (e) {
    body.innerHTML = `<tr><td colspan="9" class="hint">${e.message}</td></tr>`;
  }
}

async function probeAll() {
  const rows = $$('#targetRows [data-probe]');
  toast(`开始测试 ${rows.length} 个目标…`);
  for (const b of rows) {
    b.disabled = true; b.textContent = '…';
    try {
      const r = await api(`/api/targets/${b.dataset.probe}/probe`, { method: 'POST' });
      b.textContent = r.ok ? `${fmtMs(r.ms)}ms` : '超时';
      b.classList.toggle('danger', !r.ok);
    } catch (_) { b.textContent = '失败'; }
    await new Promise(r => setTimeout(r, 120));
  }
  toast('全部测试完成，点击刷新可重置');
}

/* ---------------- dialog ---------------- */
function openTargetDialog() {
  $('#formTarget').reset();
  $('#fPort').value = 443;
  $('#dlgTarget').showModal();
}

async function submitTarget(ev) {
  ev.preventDefault();
  const body = {
    name: $('#fName').value.trim(),
    host: $('#fHost').value.trim(),
    port: Number($('#fPort').value) || 443,
    proto: $('#fProto').value,
    group: $('#fGroup').value.trim(),
  };
  if (!body.name || !body.host) { toast('名称与主机必填', true); return; }
  try {
    await api('/api/targets', { method: 'POST', body });
    $('#dlgTarget').close();
    toast(`已添加 ${body.name}`);
    loadTargets();
    loadTargetPicker();
  } catch (e) { toast(e.message, true); }
}

/* ---------------- boot ---------------- */
function boot() {
  $$('#nav button').forEach(b => b.onclick = () => switchView(b.dataset.view));
  $$('.chip[data-preset]').forEach(c => c.onclick = () => setDuration(Number(c.dataset.preset)));
  $('#btnStart').onclick = startTest;
  $('#btnStop').onclick = stopTest;
  $('#btnRefreshSessions').onclick = loadSessions;
  $('#btnPurge').onclick = async () => {
    const days = Number(prompt('删除多少天之前的会话？', '90'));
    if (!days || days <= 0) return;
    try {
      const r = await api(`/api/maintenance/purge?days=${days}`, { method: 'POST' });
      toast(`已清理 ${r.removed_sessions} 个会话`);
      loadSessions(); refreshDbPill();
    } catch (e) { toast(e.message, true); }
  };

  // 分析页
  $('#anSession').onchange = loadAnalysisSeries;
  $('#anWindow').onchange = loadAnalysisWindow;
  $('#anMode').onchange = () => {
    const win = analysisMode() === 'window';
    $('#anSessionWrap').style.display = win ? 'none' : '';
    $('#anWindowWrap').style.display = win ? '' : 'none';
    loadAnalysisSeries();
  };
  $('#btnAnalyze').onclick = loadAnalysisSeries;
  $('#btnHeat').onclick = loadHeatmap;
  $('#heatBucket').onchange = loadHeatmap;
  $('#heatDays').onchange = loadHeatmap;
  $('#btnTrend').onclick = loadLongTrend;
  $('#btnCompare').onclick = compareTargets;
  $('#btnNoteCompare').onclick = compareNotes;
  $('#btnAddTarget').onclick = openTargetDialog;
  $('#formTarget').onsubmit = submitTarget;
  $('#btnProbeAll').onclick = probeAll;

  // 定时与告警
  $('#btnAddSched').onclick = openScheduleDialog;
  $('#formSched').onsubmit = submitSchedule;
  $('#btnAlertSeen').onclick = markAlertsSeen;
  $('#btnAlertRefresh').onclick = loadAlerts;
  $('#alertPill').onclick = () => switchView('schedules');
  $('#btnImportFile').onclick = () => $('#importFile').click();
  $('#importFile').onchange = (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (f) importCsv(f);
    ev.target.value = '';
  };

  refreshDbPill();
  loadTargetPicker().then(() => {
    // 页面刷新时若后台仍在跑，自动接上实时视图
    api('/api/session/live').then(d => {
      if (d.running) { beginPolling(); switchView('live'); }
      else if (d.session_id) { renderLive(d); }
    }).catch(() => {});
  });
  // 告警角标定期刷新
  setInterval(loadAlerts, 30000);
}

document.addEventListener('DOMContentLoaded', boot);
