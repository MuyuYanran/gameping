const { chromium, devices } = require('playwright');
const BASE = 'http://127.0.0.1:8848';
(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage({ viewport: { width: 1500, height: 950 } });
  await page.goto(BASE, { waitUntil: 'networkidle' });
  await page.waitForTimeout(2500);

  // 桌面：表格/卡片是否有溢出或重叠
  const desktop = await page.evaluate(() => {
    const bad = [];
    document.querySelectorAll('.card, .tcard, table').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > window.innerWidth + 2) {
        bad.push(el.className + ' right=' + Math.round(r.right));
      }
    });
    return {
      overflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
      offenders: bad.slice(0, 5),
    };
  });

  // 目标页：基线输入可编辑、DNS 状态列有徽标
  await page.click('nav button[data-view="targets"]');
  await page.waitForTimeout(1500);
  await page.click('#btnDnsCheck');
  await page.waitForTimeout(6000);
  const targets = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('#dnsRows tr')];
    const statuses = rows.map(r => r.lastElementChild?.innerText?.trim()).filter(Boolean);
    const bw = [...document.querySelectorAll('[data-baseline]')].map(i => i.offsetWidth);
    return { dnsStatuses: [...new Set(statuses)], baselineWidths: [...new Set(bw)] };
  });

  // 移动端：触控目标尺寸
  const ctx = await browser.newContext({ ...devices['iPhone 14'] });
  const mp = await ctx.newPage();
  await mp.goto(BASE, { waitUntil: 'networkidle' });
  await mp.waitForTimeout(2000);
  const mobile = await mp.evaluate(() => {
    const btns = [...document.querySelectorAll('button.btn, nav button')];
    const small = btns.filter(b => { const r = b.getBoundingClientRect();
      return r.width > 0 && r.height < 32; }).length;
    const fontSizes = [...document.querySelectorAll('input[type=text], input[type=number]')]
      .map(i => getComputedStyle(i).fontSize);
    return {
      viewport: [window.innerWidth, window.innerHeight],
      overflowX: document.documentElement.scrollWidth > window.innerWidth + 2,
      smallTouchTargets: small,
      inputFontSizes: [...new Set(fontSizes)],
      liveGridCols: getComputedStyle(document.querySelector('.tgrid')).gridTemplateColumns,
    };
  });
  await ctx.close();

  console.log(JSON.stringify({ desktop, targets, mobile }, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
