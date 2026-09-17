"""项目铁律检查。

CLAUDE.md 里的约束分两类落地：

- **能靠「看一个文件」判定的**（Pydantic v1 遗留用法、事件循环上的阻塞调用、
  未用导入……）交给 ruff，配置在 pyproject.toml 的 ``[tool.ruff.lint]``。
- **ruff 表达不了的**在本文件：配置项与文档的一致性、死配置、异常日志丢栈。

两边不重叠。新增铁律前先问一句「ruff 能不能查」，能就别写在这里。

## 基线棘轮

规则建立之前就存在的违规登记在 ``test/conventions_baseline.toml``，被放行，
好让提交门禁今天就是绿的。但每条规则同时保证：

1. 出现基线之外的新违规 → 失败（挡住回归）
2. 基线里某项已被修好 → 也失败，要求把它从基线里删掉

第 2 条是关键。没有它，基线会腐烂成永久豁免。

## 规则自带 fixture

每条规则都配一对「已知违规样本必须被抓到 / 干净样本必须不报」的测试。
检查器本身是没人测的检查器，正是它烂掉的方式——写这个文件时我第一版的正则
假设配置项是小写，跑出来「字段总数 0、问题 0」，一个完美的假保障。
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest

from migration_scope import V2_TABLES

pytestmark = pytest.mark.conventions

REPO = Path(__file__).resolve().parent.parent
CONFIG_PY = REPO / "config.py"
ENV_EXAMPLE = REPO / ".env.example"
CLAUDE_MD = REPO / "CLAUDE.md"

# 扫描范围：应用代码 + 顶层模块 + 运维脚本 + ops-bot（独立服务，见
# openspec/changes/add-feishu-ops-bot）。迁移文件由 alembic 生成，不适用这些规则。
SCAN_ROOTS = ("app", "scripts", "ops_bot")
SCAN_TOP_LEVEL = ("config.py", "database.py", "main.py", "monitoring.py")


def _baseline() -> dict:
    return tomllib.loads((REPO / "test" / "conventions_baseline.toml").read_text("utf-8"))


def _python_files() -> list[Path]:
    files = [REPO / name for name in SCAN_TOP_LEVEL if (REPO / name).exists()]
    for root in SCAN_ROOTS:
        files.extend(
            p for p in (REPO / root).rglob("*.py") if "__pycache__" not in p.parts
        )
    return sorted(files)


# --------------------------------------------------------------------------
# 规则实现（纯函数，接受源码文本，便于用 fixture 正反两面测）
# --------------------------------------------------------------------------


def find_lossy_exception_logging(source: str) -> list[int]:
    """找出 ``except ... as e:`` 块里把异常变量传给 ``logger.error`` 的调用。

    返回行号列表。``logger.error("...: %s", exc)`` 只留下一行消息，栈没了；
    ``logger.exception`` 才会带栈。CLAUDE.md：「栈是这条路径唯一的线索来源」。
    """
    found: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler) or not node.name:
            continue
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "error"
                and any(
                    isinstance(n, ast.Name) and n.id == node.name for n in ast.walk(sub)
                )
            ):
                found.append(sub.lineno)
    return sorted(found)


def find_missing_path_bootstrap(source: str) -> set[str]:
    """找出 import 了仓库根模块、却没把仓库根放上 ``sys.path`` 的脚本。

    返回被 import 的根模块名集合（干净的返回空集）。

    ``python scripts/x.py`` 放上 sys.path 的是 ``scripts/``，不是仓库根，于是
    ``from app... import`` 直接 ``ModuleNotFoundError``。scripts/ 下多数文件已有
    ``sys.path.insert(0, ...parent.parent)`` 这行；缺它的表现是脚本在别人的 cron
    里跑不起来，而本地 ``PYTHONPATH=.`` 跑得好好的——差别只在调用方式，不在代码。

    函数体内的延迟 import 同样算数：prune_retention 正是把 import 写在 ``main()``
    里，于是 ``--help`` 正常、真跑才炸。
    """
    tree = ast.parse(source)
    root_modules = {"app", "config", "database", "main", "monitoring"}
    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(
                a.name.split(".")[0] for a in node.names
                if a.name.split(".")[0] in root_modules
            )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            head = node.module.split(".")[0]
            if head in root_modules:
                imported.add(head)

    if not imported:
        return set()

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "insert"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "path"
            and isinstance(node.func.value.value, ast.Name)
            and node.func.value.value.id == "sys"
        ):
            return set()

    return imported


def _is_class_var(annotation: ast.expr) -> bool:
    """这个注解是不是 ``ClassVar[...]``（含 ``typing.ClassVar[...]``）。"""
    node = annotation.value if isinstance(annotation, ast.Subscript) else annotation
    if isinstance(node, ast.Attribute):
        return node.attr == "ClassVar"
    return isinstance(node, ast.Name) and node.id == "ClassVar"


def settings_fields(config_source: str) -> dict[str, ast.expr | None]:
    """从 config.py 取出 ``Settings`` 的字段名 → 默认值 AST（无默认值则 None）。

    用 AST 而不是正则：正则要靠缩进宽度和大小写猜，猜错就静默返回空集合。

    **``ClassVar`` 不算配置项。** pydantic 明确不把它当字段，环境变量喂不进去。把它
    要求进 .env.example 会得到一行「配了也不生效」的假配置——正是这些规则要消灭的东西。
    """
    tree = ast.parse(config_source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                stmt.target.id: stmt.value
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign)
                and isinstance(stmt.target, ast.Name)
                and not _is_class_var(stmt.annotation)
            }
    raise AssertionError("config.py 里找不到 Settings 类")


def _literal(node: ast.expr | None):
    """能求值成字面量就返回，否则 None（如 3 * 1024 * 1024 会被求值成 int）。"""
    if node is None:
        return None
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError):
        return None


def find_dead_settings(config_source: str, consumer_sources: list[str]) -> set[str]:
    """找出定义了却无人读取的配置项。

    两处消费都算数，缺一会误报：

    - 外部模块的 ``settings.FIELD``
    - config.py 内部 property 里的 ``self.FIELD``（ENABLED_SITES 与 DEFAULT_SITE
      就只经 ``enabled_site_codes`` / ``default_site`` 两个 property 消费）

    field_validator 的 ``@field_validator("FIELD")`` 不算消费——校验器只是约束
    取值，不代表这个值被任何逻辑用到。
    """
    fields = set(settings_fields(config_source))
    referenced: set[str] = set()

    for src in consumer_sources:
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "settings":
                    referenced.add(node.attr)

    # config.py 自身：property / 方法体里的 self.FIELD
    for node in ast.walk(ast.parse(config_source)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            referenced.add(node.attr)

    return fields - referenced


def find_settings_doc_drift(
    config_source: str, env_example: str, claude_md: str
) -> dict[str, dict[str, object]]:
    """找出 config.py 默认值与 .env.example / CLAUDE.md 公示值不一致的配置项。

    只比对**数值与布尔**默认值。字符串默认值多是占位符或密钥（``OSS_BUCKET=""``
    对 ``OSS_BUCKET=your-bucket``），不一致是正常的。

    为什么要求相等：.env.example 是照抄进 .env 的模板，CLAUDE.md 是运维依据。
    若 .env 漏配某项，生效的是 config.py 默认值——两者不同，就意味着文档承诺的
    运行参数和实际生效的不是一回事，且没有任何报错提示。
    """
    drift: dict[str, dict[str, object]] = {}

    for name, default_node in settings_fields(config_source).items():
        default = _literal(default_node)
        if not isinstance(default, (int, bool)):
            continue

        for label, text in (("`.env.example`", env_example), ("CLAUDE.md", claude_md)):
            for raw in re.findall(rf"^\s*{name}\s*=\s*(\S+)", text, re.M):
                documented = raw.split("#")[0].strip().strip("\"'")
                if not documented:
                    continue
                if isinstance(default, bool):
                    parsed: object = documented.lower() in ("true", "1", "yes")
                else:
                    try:
                        parsed = int(documented)
                    except ValueError:
                        continue
                if parsed != default:
                    drift[name] = {
                        "default": default,
                        "documented": parsed,
                        "where": label,
                    }
    return drift


def declared_table_names(model_sources: list[str]) -> set[str]:
    """从模型源码里取出所有 ``__tablename__`` 字面量。"""
    names: set[str] = set()
    for source in model_sources:
        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Assign):
                continue
            if not any(getattr(t, "id", None) == "__tablename__" for t in node.targets):
                continue
            value = _literal(node.value)
            if isinstance(value, str):
                names.add(value)
    return names


def find_tables_outside_migration_scope(
    model_sources: list[str], listed: frozenset[str] | set[str]
) -> set[str]:
    """找出声明了 ``__tablename__`` 却没登记进 ``V2_TABLES`` 的表。

    ``migration_scope.include_object`` 是白名单：不在清单里的表，autogenerate 既不
    比对也不报错，对模型改动的反应是「没有差异」——与「确实没改」无法区分。
    ``dim_brand`` / ``dim_category`` / ``fact_dropped`` 都曾这样漏过。

    **只查这一个方向。** 反过来「清单里有、模型里没有」是正常的：``role_permission``
    是 ``Table(...)`` 声明的多对多关联表，没有 ``__tablename__``。
    """
    return declared_table_names(model_sources) - set(listed)


# --------------------------------------------------------------------------
# 规则自测：违规样本必须被抓到，干净样本必须不报
# --------------------------------------------------------------------------

_LOSSY_SAMPLE = """
import logging
logger = logging.getLogger(__name__)

def bad():
    try:
        risky()
    except ValueError as exc:
        logger.error("失败: %s", exc)

def also_bad():
    try:
        risky()
    except OSError as e:
        logger.error(f"失败 {e}")
"""

_CLEAN_SAMPLE = """
import logging
logger = logging.getLogger(__name__)

def good():
    try:
        risky()
    except ValueError:
        logger.exception("失败")

def also_good():
    try:
        risky()
    except OSError as exc:
        raise RuntimeError("包装") from exc

def unrelated():
    logger.error("这不在 except 块里，与异常无关")
"""


def test_rule_catches_lossy_exception_logging():
    assert find_lossy_exception_logging(_LOSSY_SAMPLE) == [9, 15]


def test_rule_ignores_correct_exception_logging():
    assert find_lossy_exception_logging(_CLEAN_SAMPLE) == []


_SCRIPT_NO_BOOTSTRAP = """
import argparse

def main():
    from app.table.analysis import retention_service
    from database import SessionFactory
    return 0
"""

_SCRIPT_WITH_BOOTSTRAP = """
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.table.analysis import retention_service
from database import SessionFactory
"""

_SCRIPT_STDLIB_ONLY = """
import argparse
import csv
from datetime import date

def main():
    return 0
"""


def test_rule_catches_script_missing_path_bootstrap():
    """函数体内的延迟 import 也要抓到——真实案例正是这一种。"""
    assert find_missing_path_bootstrap(_SCRIPT_NO_BOOTSTRAP) == {"app", "database"}


def test_rule_ignores_bootstrapped_and_stdlib_only_scripts():
    assert find_missing_path_bootstrap(_SCRIPT_WITH_BOOTSTRAP) == set()
    assert find_missing_path_bootstrap(_SCRIPT_STDLIB_ONLY) == set()


_CONFIG_SAMPLE = """
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    USED_DIRECTLY: int = 5
    USED_VIA_PROPERTY: str = "US"
    NEVER_READ: int = 99

    @property
    def parsed(self) -> str:
        return self.USED_VIA_PROPERTY
"""


def test_settings_fields_skips_class_vars():
    """``ClassVar`` 不是配置项，不能被要求写进 .env.example。

    pydantic 不把它当字段，环境变量喂不进去——要求它出现在模板里，等于教人写一行
    「配了也不生效」的假配置。
    """
    source = """
from typing import ClassVar
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    REAL_SETTING: int = 5
    NOT_A_SETTING: ClassVar[int] = 300
    ALSO_NOT: typing.ClassVar[str] = "x"
"""
    assert set(settings_fields(source)) == {"REAL_SETTING"}


def test_rule_catches_dead_setting_but_not_property_backed():
    """property 支撑的字段不能误报——这是最容易写错的一处。

    真实案例：ENABLED_SITES / DEFAULT_SITE 只经 property 消费，裸 grep
    ``settings.FIELD`` 会把它们双双报成死配置。
    """
    dead = find_dead_settings(_CONFIG_SAMPLE, ["print(settings.USED_DIRECTLY)"])
    assert dead == {"NEVER_READ"}


def test_rule_catches_doc_drift_and_ignores_matching_values():
    config = """
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    POOL: int = 20
    TIMEOUT: int = 30
    NAME: str = ""
"""
    drift = find_settings_doc_drift(
        config,
        env_example="POOL=8\nTIMEOUT=30\nNAME=whatever\n",
        claude_md="",
    )
    assert set(drift) == {"POOL"}
    assert drift["POOL"]["default"] == 20
    assert drift["POOL"]["documented"] == 8


def test_rule_catches_table_outside_migration_scope():
    model = '''
class A(Base):
    __tablename__ = "listed"

class B(Base):
    __tablename__ = "forgotten"
'''
    assert find_tables_outside_migration_scope(
        [model], {"listed", "assoc_table_without_tablename"}
    ) == {"forgotten"}


def test_rule_ignores_fully_registered_models():
    model = 'class A(Base):\n    __tablename__ = "listed"\n'
    assert find_tables_outside_migration_scope([model], {"listed", "extra"}) == set()


# --------------------------------------------------------------------------
# 对真实代码库执行
# --------------------------------------------------------------------------


def test_no_new_lossy_exception_logging():
    """except 块里丢栈的日志：不得新增。"""
    baseline = _baseline()["lossy_exception_logging"]["known"]
    actual: dict[str, int] = {}
    for path in _python_files():
        hits = find_lossy_exception_logging(path.read_text("utf-8"))
        if hits:
            actual[str(path.relative_to(REPO))] = len(hits)

    new = {f: n for f, n in actual.items() if n > baseline.get(f, 0)}
    assert not new, (
        "新增了丢栈的异常日志。except 块里请用 logger.exception(...)，"
        f"不要把异常当参数传给 logger.error：{new}\n"
        f"（基线允许：{baseline}）"
    )

    fixed = {
        f: (n, actual.get(f, 0)) for f, n in baseline.items() if actual.get(f, 0) < n
    }
    assert not fixed, (
        "这些文件的丢栈日志已减少，请同步下调 test/conventions_baseline.toml 的计数，"
        f"否则基线会腐烂成永久豁免：{fixed}"
    )


def test_no_new_dead_settings():
    """config.py 定义了却无人读取的配置项：不得新增。

    死配置的害处不是浪费一行，而是文档会照着它承诺一个不存在的行为
    （MAX_FILE_SIZE 公示了 3GB 上传上限，实际没有任何代码执行这个上限）。
    """
    baseline = set(_baseline()["dead_settings"]["known"])
    consumers = [
        p.read_text("utf-8") for p in _python_files() if p != CONFIG_PY
    ]
    dead = find_dead_settings(CONFIG_PY.read_text("utf-8"), consumers)

    new = dead - baseline
    assert not new, (
        f"新增了无人读取的配置项：{sorted(new)}。"
        "要么接上消费方，要么删掉——不要留一个文档会去承诺的空配置。"
    )

    fixed = baseline - dead
    assert not fixed, (
        f"这些配置项已不再是死配置：{sorted(fixed)}，"
        "请从 test/conventions_baseline.toml 的 dead_settings 里删掉。"
    )


def test_no_new_settings_doc_drift():
    """config.py 默认值与 .env.example / CLAUDE.md 公示值不一致：不得新增。"""
    baseline = set(_baseline()["settings_doc_drift"]["known"])
    drift = find_settings_doc_drift(
        CONFIG_PY.read_text("utf-8"),
        ENV_EXAMPLE.read_text("utf-8"),
        CLAUDE_MD.read_text("utf-8"),
    )

    new = set(drift) - baseline
    assert not new, (
        "配置项的默认值与文档公示值对不上：\n"
        + "\n".join(
            f"  {k}: config.py 默认 {v['default']}，{v['where']} 写的是 {v['documented']}"
            for k, v in drift.items()
            if k in new
        )
        + "\n.env 漏配时生效的是 config.py 默认值，两者不同即意味着文档描述的运行参数并未生效。"
    )

    fixed = baseline - set(drift)
    assert not fixed, (
        f"这些配置项的文档漂移已修复：{sorted(fixed)}，"
        "请从 test/conventions_baseline.toml 的 settings_doc_drift 里删掉。"
    )


def test_no_new_scripts_missing_path_bootstrap():
    """scripts/ 下 import 仓库根模块却没做 sys.path 引导的脚本：不得新增。

    只查 scripts/：app/ 与顶层模块总是被当包 import，不存在这个问题。
    """
    baseline = set(_baseline()["missing_path_bootstrap"]["known"])
    actual = {
        str(p.relative_to(REPO))
        for p in (REPO / "scripts").rglob("*.py")
        if "__pycache__" not in p.parts and find_missing_path_bootstrap(p.read_text("utf-8"))
    }

    new = actual - baseline
    assert not new, (
        f"这些脚本 import 了仓库根的模块，却没把仓库根放上 sys.path：{sorted(new)}。"
        "照 scripts/seed_admin.py 补一行 "
        "sys.path.insert(0, str(Path(__file__).resolve().parent.parent))——"
        "否则 cron 里的 `python scripts/x.py` 会 ModuleNotFoundError，"
        "而本地 `PYTHONPATH=.` 跑得好好的。"
    )

    fixed = baseline - actual
    assert not fixed, (
        f"这些脚本已补上 sys.path 引导：{sorted(fixed)}，"
        "请从 test/conventions_baseline.toml 的 missing_path_bootstrap 里删掉。"
    )


def test_env_example_covers_every_setting():
    """每个配置项都要在 .env.example 里有一行（可以是注释掉的示例）。

    .env.example 是新环境的唯一清单。漏一项，部署时就会静默用默认值——
    对 ADMIN_SECRET_KEY 这类必填项，是生产事故的起点。
    """
    fields = set(settings_fields(CONFIG_PY.read_text("utf-8")))
    env_text = ENV_EXAMPLE.read_text("utf-8")
    missing = {
        name for name in fields if not re.search(rf"^\s*#?\s*{name}\s*=", env_text, re.M)
    }
    assert not missing, (
        f".env.example 缺少这些配置项：{sorted(missing)}。"
        "CLAUDE.md 的 guardrail：新增配置务必同步过去，含单位与安全的本地默认值。"
    )


def test_every_model_table_is_in_migration_scope():
    """每张模型表都要登记进 alembic/env.py 的 V2_TABLES。

    漏登不会报错，只会让 autogenerate 对这张表的改动一律回答「没有差异」——
    与「确实没改」无法区分。dim_brand / dim_category / fact_dropped 都曾漏过。
    """
    models = [p.read_text("utf-8") for p in _python_files() if p.name.endswith("_model.py")]
    missing = find_tables_outside_migration_scope(models, V2_TABLES)
    assert not missing, (
        f"这些表没登记进 migration_scope.py 的 V2_TABLES：{sorted(missing)}。"
        "include_object 是白名单，漏登的表 autogenerate 看不见也不报错。"
        "分区表登记父表名即可，叶子分区不要登记。"
    )
