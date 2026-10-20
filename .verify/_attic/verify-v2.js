// 5 项新功能的界面验收：卡顿评分、基线、DNS 体检、多机实例、SVG 导出、PWA、移动端
const { chromium, devices } = require('playwright');
const fs = require('fs');
const OUT = __dirname + '/shots';
fs.mkdirSync(OUT, { recursive: true });
const BASE = 'http://127.0.0.1:8848';

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const errors = [];
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  page.on('pageerror', e => errors.push('PAGEERROR: ' + e.message));
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  await page.goto(BASE, { waitUntil: 'networkidle' });

  // 1) 实时页：顺滑度 KPI + 卡片评分条
  await page.fill('#inSec', '18');
  await page.fill('#inInterval', '2');
  await page.click('#btnStart');
  await page.waitForTimeout(9000);
  const liveKpi = await page.locator('#kpiStutter').innerText();
  const scoreBars = await page.locator('#liveGrid .score-bar').count();
  await page.screenshot({ path: OUT + '/v2-live.png' });
  await page.waitForTimeout(11000);

  // 2) 目标管理页：基线列 + DNS 体检 + 实例
  await page.click('nav button[data-view="targets"]');
  await page.waitForTimeout(2000);
  const baselineInputs = await page.locator('[data-baseline]').count();
  await page.fill('[data-baseline]:first-of-type', '45');
  await page.locator('[data-baseline]').first().dispatchEvent('change');
  await page.waitForTimeout(1200);
  await page.click('#btnDnsCheck');
  await page.waitForTimeout(6000);
  const dnsRows = await page.locator('#dnsRows tr').count();
  await page.fill('#instId', 'desk');
  await page.fill('#instNote', '台式·有线');
  await page.click('#btnAddInstance');
  await page.waitForTimeout(1500);
  const instRows = await page.locator('#instRows tr').count();
  await page.screenshot({ path: OUT + '/v2-targets.png', fullPage: true });

  // 3) 历史页：SVG 导出按钮
  await page.click('nav button[data-view="history"]');
  await page.waitForTimeout(1500);
  await page.click('[data-view-session]');
  await page.waitForTimeout(2000);
  const hasSvgBtn = await page.locator('#btnExportSvg').isVisible();
  const sid = await page.evaluate(async () => {
    const r = await fetch('/api/sessions?limit=1'); const j = await r.json();
    return j.items[0].id;
  });
  const svg = await page.evaluate(async (id) => {
    const r = await fetch(`/api/sessions/${id}/chart.svg`);
    const t = await r.text();
    return { status: r.status, len: t.length, isSvg: t.startsWith('<svg') };
  }, sid);

  // 4) PWA
  const pwa = await page.evaluate(async () => {
    const man = await (await fetch('/manifest.webmanifest')).json();
    const sw = await fetch('/sw.js');
    const reg = 'serviceWorker' in navigator
      ? await navigator.serviceWorker.getRegistration().then(r => !!r).catch(() => false) : null;
    return { display: man.display, icons: man.icons.length, swStatus: sw.status, swRegistered: reg };
  });

  // 5) 移动端（iPhone 14 视口）
  const mobile = await browser.newContext({ ...devices['iPhone 14'] });
  const mp = await mobile.newPage();
  mp.on('pageerror', e => errors.push('MOBILE: ' + e.message));
  await mp.goto(BASE, { waitUntil: 'networkidle' });
  await mp.waitForTimeout(2500);
  await mp.screenshot({ path: OUT + '/v2-mobile.png' });
  const mobileNav = await mp.locator('#nav button').count();
  const overflow = await mp.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 2);
  await mobile.close();

  console.log(JSON.stringify({
    errors, liveKpi, scoreBars, baselineInputs, dnsRows, instRows,
    hasSvgBtn, svg, pwa, mobileNav, horizontalOverflow: overflow,
  }, null, 2));
  await browser.close();
})().catch(e => { console.error('FAILED', e); process.exit(1); });
