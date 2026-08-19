"""哪些数据库对象归 alembic 管。

从 ``alembic/env.py`` 拆出来只有一个理由：**env.py 导不进来**。它在导入时就取
``context.config``，在 alembic 运行时之外一 import 就炸，于是任何想核对这份清单的
测试都只能去正则或 AST 解析它的源码——而那种检查本身就很脆。放在这里之后，
测试直接 import 同一份对象，检查的和 alembic 用的是同一个东西。
"""

from __future__ import annotations

# V2 的表清单。只有这些会被 autogenerate 与迁移管理。
#
# **新增模型必须同时登记在这里。** 漏登的表 autogenerate 既看不见也不报错——它对模型
# 改动的反应是「没有差异」，一份无法与「确实没改」区分的假绿。``dim_brand`` /
# ``dim_category`` / ``fact_dropped`` 都曾这样漏了一段时间；
# ``test_conventions.py`` 现在会拦住这件事。
#
# 分区表登记的是**父表名**。叶子分区（``fact_daily_2026_08_us`` 之类）不在清单里，
# 因此不会被 autogenerate 当作「库里多出来的表」而生成 drop——这正是要用白名单
# 而不是黑名单的原因。
V2_TABLES = frozenset(
    {
        "dim_keyword",
        "dim_product",
        "dim_brand",
        "dim_category",
        "fact_daily",
        "fact_weekly",
        "fact_dropped",
        "fact_growth",
        "site_data_stats",
        "import_batch",
        "user_center",
        "role",
        "permission",
        "role_permission",
        "keyword_group",
        "keyword_group_item",
    }
)


def include_object(object_, name, type_, reflected, compare_to):
    """只放行 V2 的对象，把 V1 的遗留表挡在迁移之外。"""
    if type_ == "table":
        return name in V2_TABLES
    # 索引等从属对象跟随其所属表。
    parent = getattr(object_, "table", None)
    if parent is not None:
        return parent.name in V2_TABLES
    return True
