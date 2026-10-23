const { chromium } = require('playwright');

const HOSTS = [
  ['www.5eplay.com', 'https://www.5eplay.com/favicon.ico'],
  ['pvp.wanmei.com', 'https://pvp.wanmei.com/favicon.ico'],
  ['valorant.qq.com', 'https://valorant.qq.com/favicon.ico'],
  ['store.steampowered.com', 'https://store.steampowered.com/favicon.ico'],
  ['www.baidu.com', 'https://www.baidu.com/favicon.ico'],
];

(async () => {
  const browser = await chromium.launch({ channel: 'chrome' });
  const page = await browser.newPage();
  await page.goto('about:blank');

  // 1) Image 计时：跨域加载不需要 CORS，onload/onerror 都能拿到「响应到达」时刻
  const imgResults = await page.evaluate(async (hosts) => {
    const once = (url) => new Promise((resolve) => {
      const t0 = performance.now();
      const img = new Image();
      const done = (ok) => resolve({ ms: performance.now() - t0, ok });
      img.onload = () => done(true);
      img.onerror = () => done(false);
      img.src = url + (url.includes('?') ? '&' : '?') + 'cb=' + Math.random();
    });
    const out = {};
    for (const [name, url] of hosts) {
      const runs = [];
      for (let i = 0; i < 4; i++) runs.push(await once(url));
      const ok = runs.filter(r => r.ok);
      out[name] = {
        samples: runs.map(r => Math.round(r.ms)),
        okCount: ok.length,
        avgOk: ok.length ? Math.round(ok.reduce((a, b) => a + b.ms, 0) / ok.length) : null,
        note: ok.length ? '' : '全部被拦截或超时',
      };
      await new Promise(r => setTimeout(r, 150));
    }
    return out;
  }, HOSTS);

  // 2) fetch no-cors 计时：能发出去，但响应是 opaque，只能测「到达时刻」
  const fetchResults = await page.evaluate(async (hosts) => {
    const out = {};
    for (const [name, url] of hosts) {
      const t0 = performance.now();
      let status = 'ok';
      try {
        await fetch(url + (url.includes('?') ? '&' : '?') + 'x=' + Math.random(),
                    { mode: 'no-cors', cache: 'no-store' });
      } catch (e) { status = e.name; }
      out[name] = { ms: Math.round(performance.now() - t0), status };
    }
    return out;
  }, HOSTS);

  console.log(JSON.stringify({ imgResults, fetchResults }, null, 2));
  await browser.close();
})().catch(e => { console.error(e); process.exit(1); });
