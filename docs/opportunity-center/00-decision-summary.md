# 机会中心：决策摘要

**状态：** 待产品/技术联合评审  
**日期：** 2026-09-05

## 本次要决策什么

KeywordIQ 新增“机会中心”：围绕机会生命周期工作的多专家 Agent 工作台，而不是泛化聊天框或供应链交易 Agent。

```text
用户输入 / 确定性 Signal
  → 机会档案
  → 主理 Agent 组织专家取证
  → 风险与证据关卡
  → 人工决策
  → 内部验证与复盘
```

## 已收束的产品决策

- 输入支持自然语言、关键词、ASIN、商品链接和图片；所有输入都归入可追溯的机会档案。
- “选品机会主理 Agent”负责编排；发现、市场验证、商品/竞争、供应链可行性、知识产权风险、决策/复盘为稳定专家职责。
- 聊天仅在机会档案中用于澄清、解释和追问；收件箱、机会卡片、证据、任务和审批才是主界面。
  - **2026-11-11 用户改定：** 机会中心整体改为对话形式，参考 WorkBuddy 的「工作空间、任务、专家、定时任务」；原来的阶段 Tab 改为专家。每个任务仍是带证据、确认与人工签署的结构化对话，不是泛化聊天框：会产生费用的调用仍由人确认，结论仍由人签署。实现见 change `add-opportunity-ip-agent` 决策 10–14。
- V0/V1 禁止 Agent 自动询盘、采购、谈判、付款、调价、投广告、修改 Listing、上下架；只可生成草稿和内部任务，必须人工批准。
- 结论必须显示来源、时间范围、数据质量、未知项、冲突和可推翻条件。

## 此次必须拍板的架构决策

1. **多租户**：新增机会相关数据一律以 `tenant_id` 隔离；`site` 不能替代租户。现有事实表的迁移另行决策。
2. **分析执行方式**：V0/V1 采用业务语义层 + 受控 API/预定义查询；不开放任意 NL2SQL。
3. **Agent 运行方式**：采用数据库状态机、后台任务和受控工具编排；不引入自由循环的通用 Agent Harness。
4. **V0 数据边界**：仅使用质量通过的现有 Amazon 数据、人工附件和确定性 Signal；外部数据需逐个完成授权与连接器评审后启用。

## 关联 Issue

| 依赖 | 承接内容 |
|---|---|
| [#76](https://github.com/hisummer2406/KeywordIQ/issues/76) | 入口、权限骨架与诚实空状态 |
| [#54](https://github.com/hisummer2406/KeywordIQ/issues/54) | 主动 Signal、事件工作流与解释 |
| [#7](https://github.com/hisummer2406/KeywordIQ/issues/7) | 预计算指标与关键词波动 Signal |
| [#38](https://github.com/hisummer2406/KeywordIQ/issues/38) | 完整 ASIN 反查证据 |
| [#78](https://github.com/hisummer2406/KeywordIQ/issues/78) | Google Trends 验证边界 |
| [#24](https://github.com/hisummer2406/KeywordIQ/issues/24)、[#25](https://github.com/hisummer2406/KeywordIQ/issues/25) | 专利/商标风险数据源 |
| [#13](https://github.com/hisummer2406/KeywordIQ/issues/13) | 多租户、提醒、反馈与效果回查 |
| [#55](https://github.com/hisummer2406/KeywordIQ/issues/55) | 指标口径、数据源优先级和范围收口 |
