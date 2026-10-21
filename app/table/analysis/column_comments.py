"""跨表复用的列注释文本。

同一个含义的列出现在多张表里（``site`` 在 5 张表上、``period_type`` 在 3 张），
注释理应一字不差。写成常量而不是各处抄一遍，是因为**列注释参与 autogenerate 的比对**：
差一个字就生成一条 ``alter_column``，几十条这样的噪音足以把真正的结构差异淹掉——
``user_center`` 的时间戳少了时区，就是这么在四十行注释 diff 里躲了很久。

改这里的文本必须配一条迁移把库里的注释一起改，否则下一次 autogenerate 就会替你生成。
"""

from __future__ import annotations

SITE_COLUMN_COMMENT = "ISO 3166-1 alpha-2 大写站点码（英国用 GB 不用 UK），取值由应用层站点清单约束"
PERIOD_COLUMN_COMMENT = "周期类型：DAY 或 WEEK"
FIRST_SEEN_DATE_COMMENT = "首次入榜的报告期次日期（取自文件名日期，非上传/迁移时间）；依据未知时为空"
FIRST_SEEN_PERIOD_COMMENT = "首次入榜的日期来自日报(DAY)还是周报(WEEK)；周报来源只有周精度"
FIRST_SEEN_BASIS_COMMENT = (
    "首次入榜依据：IMPORTED 本系统导入创建 / EARLIEST_OBSERVED 最早可观测(此前可能见过) / UNKNOWN 无证据"
)
