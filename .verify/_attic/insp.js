const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ channel: 'chrome' });
  const p = await b.newPage({ viewport: { width: 1500, height: 950 } });
  await p.goto('http://127.0.0.1:8848/', { waitUntil: 'networkidle' });
  await p.click('nav button[data-view="analysis"]');
  await p.waitForTimeout(3500);
  const info = await p.evaluate(() => {
    const svg = document.querySelector('#chartSvg');
    const paths = [...svg.querySelectorAll('path')].map(p => ({
      d: (p.getAttribute('d')||'').slice(0,200),
      stroke: p.getAttribute('stroke'),
      width: p.getAttribute('stroke-width'),
      fill: p.getAttribute('fill'),
      bbox: (() => { try { const r = p.getBoundingClientRect(); return [r.x|0,r.y|0,r.width|0,r.height|0]; } catch(e){ return null; } })(),
    }));
    const vb = svg.getAttribute('viewBox');
    const css = getComputedStyle(svg);
    return { vb, h: css.height, w: css.width, paths, count: paths.length };
  });
  console.log(JSON.stringify(info, null, 2));
  await b.close();
})();
