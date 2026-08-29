> 只改屏幕路径 `/toolbox/library` → `/keyword/library`（随「工具箱」容器退休，见
> `top-nav-structure`）。判据与理由一字未动，但 MODIFIED 是整条替换，故 Scenario 照录。

## MODIFIED Requirements

### Requirement: 词库屏进屏即查当前分组

词库屏 SHALL 在进入时自动查询默认分组的最新一期，MUST NOT 等待用户点击查询按钮。

这与持续增长市场屏相反，判据是空表格的含义：增长榜的空表有三种成因（没查 / 窗口缺期 /
口径下无命中），必须先让用户做出选择；而盯盘的行集合就是用户自己的清单，空表只有一种
含义——组里没有词。

#### Scenario: 打开词库屏

- **WHEN** 具备权限的用户进入 `/keyword/library` 且已有分组
- **THEN** 自动选中一个分组并查询其最新一期，无需额外点击
