# _attic — 一次性探索脚本（勿用）

这些都是调试期间的临时产物，有效逻辑已合并进 `../verify.js`：

- `insp.js` — 检查 SVG path / viewBox 是否真的渲染。**已并入** `chartInfo`。
- `note-check.js` — 验证「网络备注」端到端。**已并入** `noteInfo` / `noteCompare`。
- `hover-check.js` — 验证曲线悬停十字线与数值提示。**已并入** `hoverInfo` / `hoverHides`。
- `verify-v2.js` — 验证顺滑度、基线、DNS 体检、多机实例、SVG 导出、PWA、移动端。
  **已并入** `baselineInputs` / `dnsRows` / `instRows` / `svgInfo` / `stutterInfo` /
  `pwaInfo` / `mobileInfo`。
- `dom-check.js` — 检查桌面与移动端的溢出、触控目标尺寸、输入框字号。
  **已并入** `mobileInfo`（`overflowX`、`smallTouchTargets`、`inputFontSizes`）。

保留仅为留档，不要直接运行。当前唯一的验收入口是 `../verify.js`。

- `_browser-ping.js` — 验证「浏览器能否测延迟」的一次性实验：用 Image 计时与
  `fetch(no-cors)` 计时对若干游戏域名取样，结论是浏览器只能测应用层 RTT
  （含 TLS 与服务端处理），与后端 TCP 握手差异很大。留档作为「浏览器模式」
  可行性判断的依据，勿直接运行。
