## ADDED Requirements

### Requirement: 前端组件库版本基线

SPA SHALL 以 antd 6 作为组件库主版本，`@ant-design/icons` 的主版本 SHALL 与 antd 保持一致（同为 6）。
项目代码 MUST NOT 混装 antd 5 和 antd 6，也 MUST NOT 混装 icons 5 和 icons 6。

如果某个依赖的 peer 声明不覆盖当前的 antd 主版本（例如 `@ant-design/pro-components` 2.x
只声明了 antd 4 和 5），这个越界 SHALL 在包管理器配置里显式放行，并写明原因和验证依据。
MUST NOT 靠忽略安装告警或关闭严格 peer 检查来让它通过。

前端生产构建 SHALL 通过 esbuild helper 冲突检查。出现冲突时，SHALL 通过调整构建产物的形态
来消除冲突，MUST NOT 以关闭检查的方式放行。如果产物形态的调整会让构建流程内置的检查不再执行，
`build` 脚本 SHALL 显式对产物运行独立的冲突检查，使 CI 和部署脚本调用 `pnpm build` 时仍然强制执行。

#### Scenario: 依赖主版本一致

- **WHEN** 检查前端的锁定依赖
- **THEN** 只有一份 antd 6.x 被项目代码解析到，`@ant-design/icons` 的主版本为 6

#### Scenario: peer 越界被显式记录

- **WHEN** 某个依赖的 peer 声明不包含 antd 6，但项目经过验证后仍然使用它
- **THEN** 包管理器配置里有一条针对这个依赖与 antd 的放行规则，旁边注明原因和验证依据；
  不存在全局放宽 peer 检查的配置

#### Scenario: 生产构建通过产物检查

- **WHEN** 运行前端生产构建（`pnpm build`）
- **THEN** 构建成功，并且对产物目录的 esbuild helper 冲突检查已经执行、输出无冲突；该检查不依赖构建流程内置的钩子，
  检查发现冲突时 `pnpm build` 以非零状态退出

### Requirement: 项目代码不使用组件库的废弃 API

项目自身的前端代码（`frontend/src`）MUST NOT 使用当前 antd 主版本标记为废弃的组件、属性或取值，
SHALL 改用对应的替代写法。依赖库内部触发的废弃告警不受本要求约束，但 SHALL 在升级依赖时复查。

整个组件被废弃、而替代组件的接口并不等价的情形不在本要求范围内（当前只有 `List`，替代品 `Listy` 是虚拟列表接口，不是等价替换）。
这类使用处 SHALL 登记在对应变更的 design 里，写明数量、位置和不迁移的理由，并在后续变更中迁移。

#### Scenario: 开发环境无项目代码触发的废弃告警

- **WHEN** 运行前端测试，或在开发环境打开各主要页面（市场分析、词库、竞对、导入、用户管理、定价、机会中心）并完成常规操作
- **THEN** 测试输出和浏览器控制台里没有由项目代码触发的 `[antd: …] … is deprecated` 告警，已登记的例外除外

#### Scenario: 新代码沿用替代写法

- **WHEN** 新增或修改前端代码，需要用到已有替代写法的废弃能力（例如 Space 的方向、Alert 的标题、弹层关闭后销毁）
- **THEN** 使用替代写法（`orientation`、`title`、`destroyOnHidden` 等），不使用废弃写法
