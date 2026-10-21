## ADDED Requirements

### Requirement: 关键词支持全部词与连续词组匹配

查询与导出 SHALL 共用 `keyword_mode`，缺省为 `ALL_TERMS`，另可选 `PHRASE`。
ALL_TERMS SHALL 小写拆词、去重，以完整词 AND 匹配，顺序不限且允许额外词；最多 8 个不同词，超限 422。
常规英文单复数 SHALL 仅在 ASCII 纯字母且不少于 3 字母时适用；不承诺不规则词形、同义词、语义或纠错。
PHRASE SHALL 做连续子串匹配，`%` 和 `_` 按字面；ALL_TERMS 无可拆词时 SHALL 退回连续子串，不能变成不筛。
匹配规则的唯一定义 SHALL 位于 keyword_match.py，列表、总数与导出保持一致。

#### Scenario: 顺序不限但所有词都需命中

- **WHEN** 输入 `faucet sink organizer` 使用全部词模式
- **THEN** `kitchen sink faucet organizer` 可命中，只包含其中部分词的关键词不命中

#### Scenario: 完整词边界与字面符号

- **WHEN** 搜索全部词 `rug`，或以连续词组搜索包含 `%` 的文字
- **THEN** 前者不因子串匹配命中 `rugby`，后者的 `%` 不作为通配符

### Requirement: 品牌排除仅按当前行的前三品牌判断

`exclude_brand_in_keyword` 为真时，系统 SHALL 排除关键词文本整词包含该行前三品牌名任一的行，
不区分大小写，品牌名按字面而非正则；NULL 或少于 2 字符的品牌不参与，不能用全站品牌集合替代。
该开关 SHALL 计入带筛选路径，false 与未传等价。界面默认启用的品牌排除 SHALL 仅在只看首次入榜时发送。

#### Scenario: 不误用其他行品牌

- **WHEN** 关键词包含某个品牌名，但该品牌不属于本行前三品牌
- **THEN** 该行不因本开关而被排除

### Requirement: 隐藏类目作为一致的可见性基线

系统 SHALL 用同一份 `HIDDEN_CATEGORY_NAMES` 排除 top1 类目命中的在榜行，并从类目选项中隐藏对应类目。
比较不区分大小写，NULL 类目保留；列表、总数和导出 SHALL 使用相同基线，不将它计为用户筛选。
掉榜行没有类目证据 SHALL 保留既有掉榜规则，不按类目黑名单排除；可见在榜总数不能直接使用未扣基线的原始统计。

#### Scenario: 类目为空与分页分界

- **WHEN** 当前切片包含隐藏类目和 NULL 类目
- **THEN** 隐藏类目行不进入可见在榜结果，NULL 行保留，总数与分页边界使用相同可见集合
