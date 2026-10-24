## MODIFIED Requirements

### Requirement: ASIN 精确匹配覆盖三个商品槽位

查询与导出 SHALL 接受一个 ASIN 集合作为筛选条件。只要关键词 `top1`/`top2`/`top3` 三个商品槽位中任意一个的 ASIN 属于该集合，该关键词即命中；MUST NOT 只匹配第一名次。

集合规则：

- 每个 ASIN 按等值匹配。服务端 SHALL 去首尾空白、转大写、忽略空项并去重。
- 去重后 SHALL 最多 100 个，超出时拒绝请求（422），MUST NOT 截断。
- MUST NOT 按 `B0` 前缀或其他格式规则拒绝编号：图书 ISBN-10 等非 `B0` 编号同样是合法 ASIN。
- 空集合视同不筛。

同一关键词命中多个槽位或多个 ASIN 时 SHALL 只出现一行、只计一次。集合中未收录的 ASIN MUST NOT 影响其余 ASIN 的命中。ASIN 集合 SHALL 与其他筛选维度按 AND 组合，列表、总数、翻页与导出使用同一口径。

此前的实现只匹配第一名次——把一个实际排在第二、第三名次的 ASIN 粘进搜索框会查不到任何
结果，而这个限制对用户不可见：界面既不提示"只查第一名"，结果为空也分不清是"这个 ASIN
确实不在任何关键词的前三里"还是"它排在第二名、搜索框查不到"。

#### Scenario: 命中非第一名次的商品

- **WHEN** 某关键词的第二名商品 ASIN 为 `B0XXXXXXXX`，用户以该 ASIN 查询
- **THEN** 该关键词出现在结果中

#### Scenario: 同一关键词的三个槽位都可能是同一次查询命中的原因

- **WHEN** 某 ASIN 同时是关键词 A 的第一名商品、关键词 B 的第三名商品
- **THEN** 两个关键词都出现在结果中

#### Scenario: 多个 ASIN 任意命中且不重复

- **WHEN** 用户以 `[第一名 ASIN, 第三名 ASIN, 未收录 ASIN]` 查询，三者中前两个都属于同一关键词
- **THEN** 该关键词只出现一行，总数只计一次，未收录 ASIN 不影响结果

#### Scenario: 超过上限

- **WHEN** 请求的 ASIN 集合去重后为 101 个
- **THEN** 请求被拒绝（422），提示最多 100 个

#### Scenario: 重复与大小写

- **WHEN** 请求为 `[" b0aaaaaaa1 ", "B0AAAAAAA1", ""]`
- **THEN** 按单个 ASIN `B0AAAAAAA1` 查询
