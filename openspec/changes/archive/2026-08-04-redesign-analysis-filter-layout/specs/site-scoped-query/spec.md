## ADDED Requirements

### Requirement: 类目筛选接受多个类目值

查询接口 SHALL 接受一组类目值，返回类目命中其中任意一个的行。传入空集合或不传
SHALL 视同不施加类目筛选。

多类目筛选 MUST NOT 突破作用域边界：结果仍只含当前站点、当前视图、当前日期的行。

系统 SHALL 对可传入的类目个数设上限，避免单次请求携带过长的类目列表拖垮查询计划。

#### Scenario: 命中任意一个所选类目

- **WHEN** 用户传入类目集合 `["Pet Supplies", "Electronics"]`
- **THEN** 结果包含类目为 `Pet Supplies` 或 `Electronics` 的行，不含其他类目的行

#### Scenario: 单个类目

- **WHEN** 用户传入类目集合 `["Electronics"]`
- **THEN** 结果只含类目为 `Electronics` 的行

#### Scenario: 空集合视同不筛

- **WHEN** 用户传入空的类目集合
- **THEN** 结果与不传类目参数时一致

#### Scenario: 多类目筛选不突破作用域

- **WHEN** 用户在站点 `GB` 日视图某一天传入多个类目
- **THEN** 结果的每一行仍属于 `GB` 站点该日的日报数据

#### Scenario: 类目筛选计入是否带筛选的判定

- **WHEN** 用户只传入非空类目集合、不带其他筛选
- **THEN** 该请求被视为带筛选，总数按带筛选的统计策略计算，而非直接读取切片总行数
