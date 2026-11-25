# 设计：采集端页面选择器由服务端下发

## 背景与边界

只解决「选择器失效后不重装插件就能修」。不做插件代码自动更新（自建 `update_url` 或商店分发留到阶段 2 扩面时再定），不下发任何会改变判定逻辑的东西（是否停止、页数、覆盖判定仍在插件代码里）。

## 决策 1：服务端用 JSON 文件，按修改时间重新读取

配置项 `COMPETITOR_COLLECTOR_SELECTORS_FILE`（默认空字符串，表示不下发）。文件格式：

```json
{
  "version": "2026-11-26a",
  "selectors": {
    "result_card": ["[data-component-type=\"s-search-result\"][data-asin]"],
    "next_page": ["a.s-pagination-next:not(.s-pagination-disabled)"],
    "seller_name": ["#p_6 li .a-text-bold"],
    "delivery": ["#glow-ingress-line2"],
    "blocked_selector": ["form[action*=\"validateCaptcha\"]"],
    "blocked_text": ["access restricted"]
  }
}
```

- 不放进 `.env` 的 JSON 字符串：改起来容易写坏，而且要重启进程。文件按 mtime 缓存，改完下一次请求就生效。
- 只认上面六个键；每个键可选，值为 1–10 个字符串，每个 1–200 字符；`version` 必填，1–64 字符。出现未知键或任一字段不合法，整份文件视为无效：返回 `selectors: null`，记录一条告警。部分生效会让运营以为改动已经上线。
- 文件无效不影响 `max_report_items`：上限仍然正常返回，插件照常用内置选择器采集。

## 决策 2：插件先用下发值，再回落内置默认

- 下发值排在内置默认之前，不是替换：下发的选择器写错时，内置默认仍有机会命中，最坏情况和没下发一样。
- 各类的用法沿用现有逻辑：`result_card` 取第一个能匹配到卡片的选择器（卡片仍读 `data-asin`）；`next_page`、`blocked_selector` 任一命中即成立；`delivery`、`seller_name` 取第一个有文字的；`blocked_text` 与内置文案合并，按不区分大小写的子串匹配。
- `blocked_text` 只当纯文本，不构造正则，避免下发内容引出回溯爆炸。
- 后台只校验形状（字符串列表、长度上限）；CSS 语法要在页面里才能校验，content script 对每个选择器 `try { querySelector }`，抛错的跳过。

## 决策 3：每轮开始时快照

每轮开始读单批上限时一起拿到选择器，写进运行状态（`selectors`、`selectorsVersion`），本轮每次 `AUTO_SCAN` 都把它带给 content script。同一轮内不会出现前几家用旧选择器、后几家用新选择器的情况；恢复时沿用快照。运行记录和设置页显示 `selectorsVersion`，无覆盖时显示「内置」，便于把试跑结果和选择器版本对上。

## 决策 4：人工采集不变

人工面板继续只用内置选择器。#205 阶段 1 约定人工采集保持原行为；需要让人工面板也用下发值时另行评估。

## 风险

- 下发的 `delivery` 选择器若命中了错误元素，可能读到错误文字：配送校验只在含目标邮编时判相符，其余为不符或未确认，都会停止整轮，不会放行错误数据。
- `blocked_selector` 写得太宽会导致每页都判拦截、整轮停止：同样是安全方向的失败，运行记录可见。
