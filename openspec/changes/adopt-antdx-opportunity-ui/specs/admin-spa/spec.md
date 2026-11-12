## ADDED Requirements

### Requirement: 对话类界面的组件与安全边界

SPA 的对话类界面（机会中心的专家任务对话、机会档案的追问）SHALL 使用与 antd 主版本匹配的 Ant Design X 作为气泡、输入框与会话列表组件，
`@ant-design/x` 的主版本 SHALL 满足 antd 6 的 peer 要求。

对话输入框的提交动作 MUST 只触发「向大模型追问」。任何会调用付费外部服务（如专利检索、取证）的动作 MUST NOT 由对话输入框的提交或回车直接触发，
SHALL 继续经过显示调用次数的确认后才入队。

大模型与外部供应商返回的文本 MUST 作为纯文本渲染，MUST NOT 经由 Markdown 或 HTML 渲染器输出。
如需渲染 Markdown，SHALL 另行变更并单独评估注入面，MUST NOT 随组件库接入顺带引入。

#### Scenario: 回车只发送追问

- **WHEN** 用户在专家任务对话的输入框里输入问题并按 Enter
- **THEN** 只创建一次追问任务，不产生检索或取证任务，也不消耗检索预算

#### Scenario: 输入法组合态下回车不提交

- **WHEN** 用户用中文输入法在输入框里选词，按 Enter 确认候选
- **THEN** 不提交，也不丢失已输入内容

#### Scenario: 大模型返回含标记的文本

- **WHEN** 大模型回答里包含 `<script>`、`<img onerror=…>` 或 Markdown 链接语法
- **THEN** 页面按字面文本显示，不产生 DOM 元素，也不渲染成链接或图片

#### Scenario: 对话输入忙时不可提交

- **WHEN** 任务有在途的排队或执行中的工作
- **THEN** 追问输入框不可提交，且界面说明原因（等当前任务完成），不发出请求
