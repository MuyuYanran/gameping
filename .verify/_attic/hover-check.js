const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ channel: 'chrome' });
  const p = await b.newPage({ viewport: { width: 1500, height: 950 }, deviceScaleFactor: 2 });
  const errs = [];
  p.on('pageerror', e => errs.push('PAGEERROR: ' + e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push(m.text()); });

  await p.goto('http://127.0.0.1:8848/', { waitUntil: 'networkidle' });
  await p.click('nav button[data-view="analysis"]');
  await p.waitForTimeout(3500);

  const svg = p.locator('#chartSvg');
  const box = await svg.boundingBox();
  // 悬停到图表中段
  await p.mouse.move(box.x + box.width * 0.5, box.y + box.height * 0.5);
  await p.waitForTimeout(600);

  const tip1 = await p.evaluate(() => {
    const t = document.querySelector('#chartTip');
    return { hidden: t.hidden, text: t.innerText, left: t.style.left, top: t.style.top };
  });
  const hoverVisible = await p.evaluate(() =>
    document.querySelector('#hoverLayer').style.display !== 'none');

  await p.screenshot({ path: 'shots/hover-mid.png', clip: {
    x: 0, y: 60, width: 1500, height: 420 } });

  // 挪到左侧
  await p.mouse.move(box.x + box.width * 0.15, box.y + box.height * 0.5);
  await p.waitForTimeout(500);
  const tip2 = await p.evaluate(() => document.querySelector('#chartTip').innerText);

  await p.screenshot({ path: 'shots/hover-left.png', clip: {
    x: 0, y: 60, width: 1500, height: 420 } });

  // 移出应隐藏
  await p.mouse.move(box.x + box.width * 0.5, box.y - 60);
  await p.waitForTimeout(400);
  const afterLeave = await p.evaluate(() => document.querySelector('#chartTip').hidden);

  console.log(JSON.stringify({ errs, hoverVisible, tip1, tip2, afterLeave }, null, 2));
  await b.close();
})().catch(e => { console.error(e); process.exit(1); });
