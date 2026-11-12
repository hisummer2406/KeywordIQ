## ADDED Requirements

### Requirement: 导入历史展示批次来源
导入历史屏 SHALL 为每个批次展示来源（人工上传 / 插件自动 / SP-API），分页查询接口 SHALL 支持按来源筛选，并与站点、周期、状态筛选同时生效。非法来源值 MUST 被拒绝。

#### Scenario: 查看自动获取的批次
- **WHEN** 运营在导入历史中按来源筛选「SP-API」
- **THEN** 只返回来源为 `SPAPI` 的批次，每行显示来源标签

#### Scenario: 来源与其他筛选同时生效
- **WHEN** 同时选择站点 US、周期 DAY、状态 FAILED、来源「插件自动」
- **THEN** 只返回同时满足四个条件的批次

#### Scenario: 非法来源值被拒
- **WHEN** 请求携带不在允许列表中的来源值
- **THEN** 接口返回参数校验错误，不返回数据
