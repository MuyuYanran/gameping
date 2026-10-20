// 界面验收脚本：跑真实测试并截图，同时收集控制台错误。
// 用法：node verify.js   （需要先启动 python -m gameping serve）
const { chromium, devices } = require('playwright');
const fs = require('fs');

const BASE = 'http://127.0.0.1:8848';
const OUT = __dirname + '/shots';
fs.mkdirSync(OUT, { recursive: true });

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  const errors = [];
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));

  await page.goto(BASE, { waitUntil: 'networkidle' });

  // 实时页：跑 1 分钟
  await page.fill('#inMin', '1');
  await page.fill('#inSec', '0');
  await page.fill('#inInterval', '2');
  await page.fill('#inNote', 'verify-run');     // 顺带验证「网络备注」链路标注
  await page.click('#btnStart');
  await page.waitForTimeout(12000);
  await page.screenshot({ path: OUT + '/live.png' });
  const liveCount = await page.locator('#liveGrid .tcard').count();

  await page.click('#btnStop');
  await page.waitForTimeout(2000);

  // 记录本次会话 ID 供后续检查
  const sessionId = await page.evaluate(async () => {
    const r = await fetch('/api/sessions?limit=1');
    const j = await r.json();
    return j.items && j.items[0] ? j.items[0].id : null;
  });

  // 分析页
  await page.click('nav button[data-view="analysis"]');
  await page.waitForTimeout(3500);
  await page.screenshot({ path: OUT + '/analysis.png' });

  const chartInfo = await page.evaluate(() => {
    const svg = document.querySelector('#chartSvg');
    const texts = [...svg.querySelectorAll('text')].map(t => t.textContent.trim());
    const path = svg.querySelector('path');
    const d = path ? path.getAttribute('d') : '';
    const ys = d.split(/[ML]/).filter(Boolean)
      .map(p => parseFloat(p.split(',')[1])).filter(v => !isNaN(v));
    return {
      ticks: texts,
      pathPoints: ys.length,
      pathYmin: ys.length ? Math.min(...ys) : null,
      pathYmax: ys.length ? Math.max(...ys) : null,
    };
  });

  // 曲线悬停读数（十字线 + 那一刻的真实数值）
  const svgBox = await page.locator('#chartSvg').boundingBox();
  await page.mouse.move(svgBox.x + svgBox.width * 0.5, svgBox.y + svgBox.height * 0.5);
  await page.waitForTimeout(500);
  const hoverInfo = await page.evaluate(() => {
    const tip = document.querySelector('#chartTip');
    const layer = document.querySelector('#hoverLayer');
    return {
      tipHidden: tip.hidden,
      crosshair: layer ? layer.style.display !== 'none' : null,
      lines: tip.hidden ? 0 : tip.innerText.split('\n').length,
      hasTime: /^\d{2}\/\d{2} \d{2}:\d{2}:\d{2}$/.test((tip.innerText || '').split('\n')[0] || ''),
    };
  });
  await page.screenshot({ path: OUT + '/hover.png',
    clip: { x: 0, y: 60, width: 1500, height: 420 } });
  await page.mouse.move(svgBox.x + svgBox.width * 0.5, svgBox.y - 80);
  await page.waitForTimeout(300);
  const hoverHides = await page.evaluate(() => document.querySelector('#chartTip').hidden);

  // 热力图
  await page.click('#btnHeat');
  await page.waitForTimeout(1800);
  await page.screenshot({ path: OUT + '/heatmap.png' });
  const heatCells = await page.locator('#heatmap td').count();

  // 跨会话趋势
  await page.click('#btnTrend');
  await page.waitForTimeout(1800);
  const trendRows = await page.locator('#longTrendRows tr').count();

  // 链路备注：应出现在下拉里，并能与自身对比（差值为 0）
  const noteInfo = await page.evaluate(async () => {
    const r = await fetch('/api/analytics/notes');
    const j = await r.json();
    return j.items.map(n => ({ note: n.note, samples: n.samples }));
  });
  let noteCompare = null;
  if (noteInfo.length) {
    noteCompare = await page.evaluate(async (note) => {
      const q = encodeURIComponent(note);
      const r = await fetch(`/api/analytics/notes/compare?a=${q}&b=${q}`);
      const j = await r.json();
      return { status: r.status, rows: (j.rows || []).length };
    }, noteInfo[0].note);
  }

  // 定时与告警
  await page.click('nav button[data-view="schedules"]');
  await page.waitForTimeout(1800);
  await page.click('#btnAddSched');
  await page.fill('#sName', '每日体检');
  await page.fill('#sTime', '08:30');
  await page.click('#sSubmit');
  await page.waitForTimeout(1800);
  const schedRows = await page.locator('#schedRows tr').count();
  await page.screenshot({ path: OUT + '/schedules.png', fullPage: true });

  // 历史页 + HTML 报告
  await page.click('nav button[data-view="history"]');
  await page.waitForTimeout(1500);
  await page.click('[data-view-session]');
  await page.waitForTimeout(2000);
  await page.screenshot({ path: OUT + '/history.png', fullPage: true });

  const reportStatus = await page.evaluate(async (sid) => {
    const r = await fetch(`/api/sessions/${sid}/report.html`);
    return { status: r.status, len: (await r.text()).length };
  }, sessionId);

  // 目标管理：基线 / DNS 体检 / 实例 / SVG 导出
  await page.click('nav button[data-view="targets"]');
  await page.waitForTimeout(2000);
  await page.screenshot({ path: OUT + '/targets.png' });

  const baselineInputs = await page.locator('[data-baseline]').count();
  await page.fill('[data-baseline]:first-of-type', '45');
  await page.locator('[data-baseline]').first().dispatchEvent('change');
  await page.waitForTimeout(800);

  await page.click('#btnDnsCheck');
  await page.waitForTimeout(6000);
  const dnsRows = await page.locator('#dnsRows tr').count();
  const dnsStatuses = await page.evaluate(() =>
    [...new Set([...document.querySelectorAll('#dnsRows tr')]
      .map(r => r.lastElementChild?.innerText?.trim()).filter(Boolean))]);

  await page.fill('#instId', 'verify-inst');
  await page.fill('#instNote', '冒烟测试');
  await page.click('#btnAddInstance');
  await page.waitForTimeout(1200);
  const instRows = await page.locator('#instRows tr').count();

  const svgInfo = await page.evaluate(async (sid) => {
    const r = await fetch(`/api/sessions/${sid}/chart.svg`);
    const t = await r.text();
    return { status: r.status, len: t.length, isSvg: t.startsWith('<svg') };
  }, sessionId);

  // 顺滑度与评分条
  const stutterInfo = await page.evaluate(async (sid) => {
    const r = await fetch(`/api/sessions/${sid}/stutter`);
    const j = await r.json();
    const first = j.items && j.items[0];
    return { status: r.status, worst: j.worst,
             score: first ? first.stutter.score : null,
             bursts: first ? first.bursts.length : null };
  }, sessionId);

  // PWA
  const pwaInfo = await page.evaluate(async () => {
    const man = await (await fetch('/manifest.webmanifest')).json();
    const sw = await fetch('/sw.js');
    const reg = 'serviceWorker' in navigator
      ? await navigator.serviceWorker.getRegistration().then(r => !!r).catch(() => false) : null;
    return { display: man.display, icons: man.icons.length,
             swStatus: sw.status, swRegistered: reg };
  });

  // 移动端布局（iPhone 14 视口）
  const mobCtx = await browser.newContext({ ...devices['iPhone 14'] });
  const mob = await mobCtx.newPage();
  mob.on('pageerror', e => errors.push('MOBILE: ' + e.message));
  await mob.goto(BASE, { waitUntil: 'networkidle' });
  await mob.waitForTimeout(2200);
  await mob.screenshot({ path: OUT + '/mobile.png' });
  const mobileInfo = await mob.evaluate(() => ({
    viewport: [window.innerWidth, window.innerHeight],
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
    smallTouchTargets: [...document.querySelectorAll('button.btn, nav button')]
      .filter(b => { const r = b.getBoundingClientRect(); return r.width > 0 && r.height < 32; }).length,
    inputFontSizes: [...new Set([...document.querySelectorAll('input[type=text], input[type=number]')]
      .map(i => getComputedStyle(i).fontSize))],
  }));
  await mobCtx.close();

  console.log(JSON.stringify({
    errors, liveCount, heatCells, trendRows, schedRows,
    sessionId, reportStatus, chartInfo, hoverInfo, hoverHides,
    noteInfo, noteCompare, baselineInputs, dnsRows, dnsStatuses,
    instRows, svgInfo, stutterInfo, pwaInfo, mobileInfo,
  }, null, 2));

  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
