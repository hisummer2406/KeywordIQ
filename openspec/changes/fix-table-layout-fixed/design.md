## Context

`analysis-screen-layout` 里「列宽必须真正生效」这条需求，把生效机制写成了"提供等于各列
宽度之和的总宽 → 表格采用固定布局"。代码注释也是这么写的。该因果在 rc-table 早期版本
成立，7.54 之后不再成立。

`lib/Table.js` 的 `mergedTableLayout`：

```js
if (tableLayout) return tableLayout;
if (fixColumn) return scrollX === 'max-content' ? 'auto' : 'fixed';
if (fixHeader || isSticky || columns.some(c => c.ellipsis)) return 'fixed';
return 'auto';
```

分析屏没有固定列、没有 `scroll.y`、没有列开 `ellipsis`，于是落到最后一行。

## Goals / Non-Goals

**Goals:** 让列宽按声明生效；把机制写进规格与注释时**说的是真实判据**；补一条能抓住
同类退化的回归。

**Non-Goals:** 不调整任何列的具体宽度；不改导入历史屏（它有 `ellipsis` 列与固定列，
本来就是 fixed）；不动增长市场屏与用户屏（它们没有声明总宽，auto 是预期形态）。

## Decisions

### 1. 显式 `tableLayout="fixed"`，而不是给某列加 `ellipsis` 凑判据

`ellipsis` 会改变单元格的文本呈现（截断 + 悬停提示），为了触发布局模式而改内容呈现是
本末倒置；而且它同样是间接依赖，下一次判据变动照样失效。显式声明是判据的第一条。

### 2. `scroll.x` 保留，但重新表述职责

两件事分开写清楚：`tableLayout` 决定 `width` 生不生效，`scroll.x` 决定窗口不够宽时横向
滚动还是压扁。历史上把两者混成一句因果，正是这次失效没被发现的原因。

### 3. 回归断言落在渲染结果上，不在 props

断言 `document.querySelector('.ant-table table').style.tableLayout === 'fixed'`。断言我们
传了什么只能证明意图；这条要证明的是 rc-table 最终采信了什么——将来判据再变，这条仍然
会红。已验证：去掉修复后该用例确实失败（`expected 'auto' to be 'fixed'`）。

### 4. vitest 超时放宽到 20 秒

不是为了掩盖慢，而是为了让红灯有意义：整屏渲染用例单条实测 4 秒上下，默认 5 秒使得
"绿还是红"取决于机器负载，且翻的用例每次不同、报的是超时。放宽后真挂住的用例照样红，
只是晚 15 秒。

## Risks / Trade-offs

- **[固定布局下内容超宽会被截断/换行]** → 各列宽度本就是按固定布局调过的，实测两屏
  渲染正常；关键词列本来就是换行显示。
- **[超时放宽会让真的死循环晚 15 秒暴露]** → 接受；门禁另有 300 秒上限兜底。

## Migration Plan

纯前端改动，随版本发布即可，无迁移、无回填。回滚即回退提交。

## Open Questions

无。
