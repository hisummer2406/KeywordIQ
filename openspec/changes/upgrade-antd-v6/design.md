## Context

前端基于 Umi Max 4.6.82 + antd 5.29.3 + `@ant-design/pro-components` 2.8.10，React 18.3.1。
全站有 29 个文件用到 pro-components，主要是 PageContainer、ProTable、ModalForm、ProForm*。

2026-11-13 在一次性分支 `probe/antd6-upgrade` 上做了探路（不合入），结论如下：

| 检查项 | 结果 |
|---|---|
| Umi 的 antd 插件 | `@umijs/plugins` 4.6.82 已经有针对 antd ≥6 的分支，不用升 Umi |
| React 版本 | 18.3.1，满足 antd 6 的要求（≥18） |
| pro-components 2.8.10 依赖的 antd 内部路径 | 共 124 个（如 `antd/lib/config-provider/SizeContext`），在 antd 6.7.0 包里全部存在 |
| `pnpm build` | Webpack 编译成功，但 Umi 的 esbuild helper 检查报告各 chunk 的压缩 helper 重名冲突。antd 5 基线上没有这个冲突。打开 `esbuildMinifyIIFE: true` 后通过 |
| 浏览器验收（用户） | 布局、ProTable 的筛选和分页、ModalForm、机会中心都正常 |

antd 6 官方迁移说明的要点：
- 只把属性标为**废弃**，不删除，删除要到 v7；
- 很多组件的 DOM 结构有调整；
- 默认开启 CSS 变量；
- Tag 去掉了默认的右边距；
- icons 必须同时升到 6。

## Goals / Non-Goals

**Goals:**

- 全站换到 antd 6.7.x 和 icons 6，构建通过且检查没有被削弱。
- pro-components 2.8.10 在 antd 6 下继续使用，越过 peer 声明这件事要显式记录。
- 清掉 `frontend/src` 里的 antd 废弃用法，开发环境的控制台里不再有项目代码触发的废弃告警。
- 依赖 antd DOM 的样式和测试要随 DOM 变化修正，保证看到的效果和测试断言的语义都不变。

**Non-Goals:**

- 不接入 Ant Design X，机会中心的对话界面不改，留给后续变更。
- 不升级 pro-components 到 3.x，不升级 Umi Max。
- 不借这次升级调整视觉风格、主题 token 或交互。Tag 间距这类默认值变化，只在它造成可见回归时才补回来。
- 不改后端、插件、数据库和生产配置。

## Decisions

### D1：先升 antd 6，不选 X 1.x

机会中心要用的 Ant Design X，主线 2.x 要求 antd ^6.1。X 1.6.1 兼容 antd 5，但已经停止更新，用了以后还得再迁一次。
探路结果说明升 antd 6 的成本可控，所以先把基线抬上去。

**备选**：用 X 1.6.1 只改机会中心。改动面更小，但等于先背一笔确定要还的技术债，所以没选。

### D2：pro-components 保持 2.8.10，用 pnpm 的 `peerDependencyRules.allowedVersions` 显式放行

pro-components 3.x 支持 antd 6，但只有 beta。2.8.10 是正式版，探路时内部路径比对和浏览器验收都通过了。
在 `frontend/pnpm-workspace.yaml` 加一条只针对 `@ant-design/pro-components` 和 antd 的放行规则，并在旁边注明原因和验证日期。
放行范围按 pro-components 的内部子包逐个列出，不用通配。

**备选**：
- 上 3.x beta：等于让生产押在 beta 版上。
- 全局关闭严格 peer 检查：以后其他依赖的越界也会被一起放过。

两者都没选。等 pro-components 3 出正式版后，单独评估要不要升级，并删掉这条放行规则。

### D3：用 `esbuildMinifyIIFE: true` 解决 helper 冲突，不关闭检查

冲突的原因是 esbuild 压缩后，各 chunk 顶层生成了同名的 helper 变量，加载顺序一变就可能互相覆盖。
IIFE 把每个产物包在独立的作用域里，从根上消除了覆盖的可能，这也是 Umi 报错时给出的建议。

**实现时发现的一个副作用**：开了 `esbuildMinifyIIFE` 以后，Umi 构建流程内置的 helper 检查会提前返回、不再执行
（`preset-umi` 的 `esbuildHelperChecker` 里有 `if (api.config.esbuildMinifyIIFE) return`），日志里也没有任何检查输出。
也就是说只加这个开关，"检查没有被关闭"就只是字面成立。所以把 Umi 自带的独立命令串到 `build` 脚本里：
`max build && max esbuildHelperChecker dist`。这个命令不受开关影响，CI 和 `deploy.sh` 调用的都是 `pnpm build`，
因此两条路径都会强制执行，发现冲突会让构建失败。

**备选**：
- 关闭 `esbuildHelperChecker`：只是把问题藏起来，运行时仍可能出现难以复现的错误。
- 只加 IIFE 开关、不串独立检查：构建内置检查会被跳过，以后产物形态再变化时没有人会发现。
- 改用 terser 压缩：构建更慢，而且偏离了 Umi 的默认链路。

两者都没选。

### D4：在本变更里一次清掉项目代码的废弃 API

antd 6 只废弃不删除，所以不清理也能跑。但要清理的理由有三个：

1. 控制台里大量废弃告警，会掩盖真正的报错。这次验收的主要依据就是"控制台没有报错"。
2. 后续 X 接入会大面积改动机会中心，先把写法统一，可以避免新旧写法并存。
3. 这些都是机械替换，有一一对应的新写法，风险低。

范围**以 antd 6.7.0 的类型定义和运行时 `warning.deprecated` 调用为准**，不照抄迁移表。实现时逐项核对后确定为：
- Space 的 `direction` 改成 `orientation`；
- Alert 的 `message` 改成 `title`；
- Modal/Drawer 的 `destroyOnClose` 改成 `destroyOnHidden`；
- Drawer 的 `width`、`height` 改成 `size`（6.x 的 `size` 接受数字，`height` 用于上下弹出的抽屉）；
- Table 分页的 `position: ['topRight']` 改成 `placement: ['topEnd']`（`Analysis/index.tsx`，通过 ProTable 透传）；
- Tag 的 `bordered={false}` 改成 `variant="filled"`；
- Spin 的 `tip` 改成 `description`；
- Descriptions 的 `labelStyle` 改成 `styles.label`；
- InputNumber 的 `addonAfter` 改成 `Space.Compact` + `Space.Addon`。

**核对依据**：除了对照类型定义，还以**前端测试输出里的 `[antd: …] … is deprecated` 告警**为准（测试渲染了大部分页面）。
第一遍只靠 grep 迁移表漏掉了 Table `pagination.position`（292 次告警）和 Drawer `height`（73 次告警），是测试输出发现的。

**登记的例外（不迁移）**：`List` 组件整体被废弃（antd 6.7 提示"下个主版本移除，请用 `Listy`"），项目里共 6 处 `<List>`（每处各带一个 `List.Item`）：
`Opportunity/IpAgent/MessageItems.tsx` 2 处、`Opportunity/IpAgent/AnalysisCard.tsx` 1 处、`Competitor/BatchAddResult.tsx` 3 处。
不迁移的理由：`Listy` 基于虚拟列表（`data` / `itemRender` / `ref`），接口与 `List` 不等价，不是改个属性名；antd 6 内仍可用，v7 才移除；
机会中心的两个文件在下一个变更（接入 Ant Design X）里会被重写，`BatchAddResult` 随后单独评估。

**不改的**：`size="middle"`（6.7.0 的 `SizeType` 仍然接受，运行时只对 `size="default"` 发警告，项目代码也没有）；
Tooltip 的 `overlay*`、`Menu.Item`（只出现在 Umi 生成的 `.umi/` 目录里，不是项目代码）；Descriptions/List 的 `bordered`（没有被废弃）。

机械替换用脚本只改指定组件开标签上的顶层属性，替换后用 grep 确认旧写法清零、新写法数量与替换前统计一致。
只替换写法，不顺带改其他东西。

**备选**：先升级，清理另开一个变更。这样 diff 更小，但验收期间告警噪音大，所以没选。

### D5：DOM 相关的样式和测试，只修"定位"，不改"语义"

- **样式**：逐条核对 `.ant-*` 选择器在 antd 6 的 DOM 下是否还能命中，做法是在 antd 6.7.0 和 rc 组件源码里确认类名仍在生成。
  核对结果：只有 Select 的 `.ant-select-selector` 失效（根节点 `.ant-select` 接管了边框、圆角、内边距和高度变量），
  `ScopeBar.less` 改成选根节点；Menu、Pagination、Layout.Sider、Table、Input 前缀容器的类都还在，`.ant-pro-*` 来自 pro-components 未变。
- **测试**：11 个测试文件用 `.ant-*` 选择器定位元素。静态核对后，Select 相关的 6 处要改：`.scope-bar__xxx .ant-select-selector` 改成 `.scope-bar__xxx`
  （`className` 本来就加在 Select 根节点上），`.ant-select-selection-item` 改成 `.ant-select-content-value`，ImportModal 里改为直接在根节点上 `mouseDown`。
  其余因 DOM 变化失败的，只改定位方式，优先改用 role 或文本查询；断言的业务含义不能变，不重写还能通过的测试。

## Risks / Trade-offs

- **[风险] pro-components 在验收没覆盖到的路径上出现运行时问题**（例如 ProTable 的列设置、ProForm 的少见字段类型）→ 验收清单覆盖所有用到 ProTable/ModalForm 的页面；出问题时，先在这条放行规则下定位到具体组件，再决定是局部绕开还是回滚。
- **[风险] 默认开启 CSS 变量、组件 DOM 调整，造成细微的视觉变化**（例如 Tag 间距、Modal 遮罩）→ 浏览器验收逐页对比；只在出现可见回归时，通过 ConfigProvider 或局部样式补回来。
- **[风险] 测试依赖 antd 内部 DOM，升级后成批变红** → 按 D5 只改定位方式；改动后的测试在 antd 5 基线下应该也能写得出来，用这一点防止"改断言迁就实现"。
- **[取舍] 产物体积**：探路时 dist 从 4.3MB 增加到 4.5MB（未压缩，含 IIFE 包装）。可以接受，不单独优化。
- **[取舍] 废弃 API 一次清完**让 diff 变大。原计划按"依赖与配置 / 废弃 API / 样式与测试"分开提交，但提交门禁每次都跑前端 vitest，
  依赖升级单独提交时测试必然是红的，所以代码合成一个提交（OpenSpec 文档单独一个提交），评审时按 tasks 的分组对照看 diff。

## Migration Plan

1. 本变更只涉及前端：没有数据库迁移，不需要改 `.env`，后端和插件都不用动。
2. 合入 v2 后，在用户明确要求发布时，通过 `Deploy V2 to ECS` 工作流发布。产物只是前端的静态文件。
3. **回滚**：revert 合并提交后重新发布前端即可，不涉及数据或接口兼容问题。

## Open Questions

- pro-components 3 出正式版后是否升级，届时另开变更评估。到那时要同时删掉 D2 的放行规则。
