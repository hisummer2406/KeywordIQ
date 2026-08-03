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

pytestmark = pytest.mark.conventions

REPO = Path(__file__).resolve().parent.parent
CONFIG_PY = REPO / "config.py"
ENV_EXAMPLE = REPO / ".env.example"
CLAUDE_MD = REPO / "CLAUDE.md"

# 扫描范围：应用代码 + 顶层模块 + 运维脚本。迁移文件由 alembic 生成，不适用这些规则。
SCAN_ROOTS = ("app", "scripts")
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


def settings_fields(config_source: str) -> dict[str, ast.expr | None]:
    """从 config.py 取出 ``Settings`` 的字段名 → 默认值 AST（无默认值则 None）。

    用 AST 而不是正则：正则要靠缩进宽度和大小写猜，猜错就静默返回空集合。
    """
    tree = ast.parse(config_source)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            return {
                stmt.target.id: stmt.value
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
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
