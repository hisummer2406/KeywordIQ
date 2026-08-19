## 1. 存储模块

- [ ] 1.1 新建 `pages/Analysis/savedMode.ts`：`SAVED_MODE_VERSION` 常量、payload 类型
      （`{version, site, view, filters, sort}`，`filters` 不含 `q`）、键名生成
      `keywordiq.analysis.savedMode.v1.<userId>`
- [ ] 1.2 `readSavedMode(userId)`：userId 为空返回 `null`（不读任何键）；JSON 解析失败或
      版本不符则**删掉那条并返回 `null`**，不做形状迁移（决策 3）
- [ ] 1.3 `writeSavedMode(userId, payload)` / `clearSavedMode(userId)`：userId 为空时都是
      空操作。写入用 try/catch 包住——隐私模式下 `setItem` 会抛，不能让整屏崩（决策 7）
- [ ] 1.4 文件头注释写明三级优先级、以及**为什么这份要活过 `doLogout`**（按用户分键，
      与无主的会话快照待遇不同）

## 2. 条件初始化接线

- [ ] 2.1 `index.tsx` 取当前用户 id（`useModel('@@initialState')` 的 `currentUser?.id`），
      挂载时同步读一次保存项，得到 `saved`
- [ ] 2.2 草稿初值改为 `saved` 优先：`useCachedState('filters', () => saved?.filters ?? defaultFilters())`，
      `scope` 与 `sortDraft` 同理。**`if (restored) return;` 仍在最前**，快照那条路一个请求都不发
- [ ] 2.3 挂载 effect 的作用域解析改为 `reloadScopeMeta(saved?.site, saved?.view ?? 'DAY')`，
      随后 `apply` 用 `saved?.filters ?? defaultFilters()`
- [ ] 2.4 站点失效回退：该次 `/scope` 调用带 `skipErrorHandler: true`，失败则回退默认站点
      重新解析、`clearSavedMode`、并**自行呈现**「保存的站点已下线，已切回默认站点」
      （决策 5——跳过全局提示就必须自己说，静默回退比报错更糟）

## 3. 界面

- [ ] 3.1 过滤区按钮行加「保存当前模式」，位置在「重置条件」之后。点击存
      `appliedRef.current` 派生出的 payload（**生效条件，非草稿**），成功/失败都给提示
- [ ] 3.2 `appliedRef.current` 为空（作用域无可查日期、一次都没查成）时该按钮禁用，
      不保存空条件
- [ ] 3.3 `ScopeBar` 加「我的默认」标记，仅在本次进屏确实应用了保存的条件时显示；
      保存项被丢弃（版本过期/站点失效）时不显示
- [ ] 3.4 `resetFilters` 连带 `clearSavedMode(userId)`，**不加二次确认**（决策 6）。
      只改筛选，不动站点/视图/排序
- [ ] 3.5 按钮行下边距：当前 `margin: '20px -24px -12px'` + `padding: '16px 0 4px'`，
      按钮上方 16px 下方仅 4px。**先用 `getBoundingClientRect` 实测再改，连祖先容器一起量**
      ——这一屏的像素问题有过连错三轮的记录，不许照着数值推算

## 4. 登出边界

- [ ] 4.1 `app.tsx` 的 `doLogout` 保持不清保存项，并加注释写明它与 `clearSnapshot()`
      待遇不同的理由（按用户分键 vs 无主单份），避免后来者「顺手补全」

## 5. 测试

- [ ] 5.1 `savedMode.test.ts`（新增）：读写清往返、userId 为空时不读不写、版本不符则丢弃
      并删除、JSON 损坏则丢弃、`setItem` 抛异常时 `writeSavedMode` 不外抛
- [ ] 5.2 `savedMode.test.ts`：甲的键与乙的键互不可见（同一 storage 下按 userId 分隔）
- [ ] 5.3 `index.test.tsx`：三级优先级——①无快照无保存 → 内置默认（只看新词）；
      ②无快照有保存 → 用保存的；③有快照 → 用快照且**一个请求都不发**（保存项不得穿透短路）
- [ ] 5.4 `index.test.tsx`：保存的站点/视图参与首屏解析（`getScope` 收到的是保存的站点）
- [ ] 5.5 `index.test.tsx`：站点失效 → `getScope` 报错 → 回退默认站点、保存项被清、
      界面给出说明
- [ ] 5.6 `index.test.tsx`：点「保存当前模式」存的是**生效条件**——改草稿不点查询就保存，
      存下的仍是生效那份
- [ ] 5.7 `index.test.tsx`：`appliedRef` 为空时按钮禁用
- [ ] 5.8 `index.test.tsx`：「重置条件」后保存项被删除（再次挂载走内置默认）
- [ ] 5.9 `index.test.tsx`：应用了保存条件时显示「我的默认」，没保存过时不显示
- [ ] 5.10 `cd frontend && pnpm test` 全绿；`pnpm lint` 与 `pnpm tsc` 通过
- [ ] 5.11 `uv run pytest test/ -q`——本变更无后端改动，但改了 `CLAUDE.md`。
      **注意本地 `.env` 的 `GROWTH_MIN_DAILY_RATE=0.05` 会让 2 条 growth 集成测试红，
      那是既有问题、与本变更无关**（CI 用 `.env.example` 的 0.10，是绿的）

## 6. 文档与验收

- [ ] 6.1 `CLAUDE.md` 分析查询屏一节：补三级优先级（会话快照 > 保存的条件 > 内置默认）、
      保存的存活边界（活过登出、按用户分键、版本过期即弃）、「重置」连带删除
- [ ] 6.2 手动验证：起 dev server，①调好条件点「保存当前模式」→ 登出 → 重新登录，
      条件与「我的默认」标记都在；②点「重置条件」→ 登出重登，回到只看新词、标记消失；
      ③切走再切回仍零请求；④按钮行下边距目视对称
- [ ] 6.3 `openspec validate --changes` 通过
