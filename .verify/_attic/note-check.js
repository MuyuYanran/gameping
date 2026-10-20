const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ channel: 'chrome' });
  const p = await b.newPage({ viewport: { width: 1500, height: 950 } });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });
  await p.goto('http://127.0.0.1:8848/', { waitUntil: 'networkidle' });

  // 只选 3 个目标，标上「手机热点」，跑 20 秒
  await p.fill('#inMin', '0');
  await p.fill('#inSec', '20');
  await p.fill('#inInterval', '3');
  await p.fill('#inNote', '手机热点');
  for (const name of ['完美世界·竞技', '5E·官网', '无畏契约·官网']) {
    await p.click(`.chip[data-tid]:has-text("${name}")`);
  }
  await p.click('#btnStart');
  await p.waitForTimeout(8000);
  await p.screenshot({ path: 'shots/note-live.png' });
  await p.waitForTimeout(14000);

  // 历史页应显示备注标签
  await p.click('nav button[data-view="history"]');
  await p.waitForTimeout(2000);
  const rowText = await p.locator('#sessionRows tr').first().innerText();
  await p.screenshot({ path: 'shots/note-history.png' });

  // 分析页：链路对比下拉应有「手机热点」
  await p.click('nav button[data-view="analysis"]');
  await p.waitForTimeout(2500);
  const noteOpts = await p.locator('#noteA option').allInnerTexts();
  await p.screenshot({ path: 'shots/note-analysis.png' });

  const api = await p.evaluate(async () => {
    const r = await fetch('/api/analytics/notes');
    return await r.json();
  });

  console.log(JSON.stringify({ errs, rowText, noteOpts, api }, null, 2));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
