"""受限执行器：封闭的动作枚举 + 写死 argv 的 subprocess 调用。

**不接受自由字符串。** 每个动作函数签名只接受强类型参数；没有任何路径把聊天文本
拼进命令行或环境变量，也不存在"传一个 shell 片段进来执行"这种入口
（design.md D1）。命令用 `subprocess.run(..., shell=False)`，参数以列表形式给出。

本模块假设进程运行在仓库根目录（host 级 systemd 服务的 `WorkingDirectory`，
见 `etc/keywordiq-ops-bot.service`）——`docker compose`/`./deploy.sh` 都是相对当前
目录的命令，这是 D1 从 docker-compose 服务改为 host 级服务的直接原因。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum

_OUTPUT_TRUNCATE_BYTES = 8192
_DEFAULT_TIMEOUT_SECONDS = 300
# 构建镜像 + 前端产物是分钟级操作，300 秒的通用超时对它不够，见下方 deploy()。
_DEPLOY_TIMEOUT_SECONDS = 1800


class OpsAction(StrEnum):
    RESTART_APP = "RESTART_APP"
    RELOAD_NGINX = "RELOAD_NGINX"
    BACKUP_NOW = "BACKUP_NOW"
    DEPLOY = "DEPLOY"


@dataclass
class ExecutionResult:
    success: bool
    output: str
    failed_stage: str | None = None


def _truncate(text: str) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= _OUTPUT_TRUNCATE_BYTES:
        return text
    return encoded[:_OUTPUT_TRUNCATE_BYTES].decode("utf-8", errors="ignore") + "\n...(截断)"


def _run_steps(steps: list[tuple[list[str], str]], timeout: int) -> ExecutionResult:
    """依次跑一串命令，任一步失败立即停止并标注失败阶段。"""
    outputs: list[str] = []
    for argv, stage in steps:
        try:
            proc = subprocess.run(
                argv, cwd=".", capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired:
            outputs.append(f"$ {' '.join(argv)}\n(超时 {timeout}s)")
            return ExecutionResult(False, _truncate("\n".join(outputs)), stage)
        except OSError as exc:
            outputs.append(f"$ {' '.join(argv)}\n(启动失败: {exc})")
            return ExecutionResult(False, _truncate("\n".join(outputs)), stage)

        outputs.append(f"$ {' '.join(argv)}\n{proc.stdout}{proc.stderr}")
        if proc.returncode != 0:
            return ExecutionResult(False, _truncate("\n".join(outputs)), stage)

    return ExecutionResult(True, _truncate("\n".join(outputs)), None)


def restart_app() -> ExecutionResult:
    return _run_steps([(["docker", "compose", "restart", "app"], "RESTART_APP")], _DEFAULT_TIMEOUT_SECONDS)


def reload_nginx() -> ExecutionResult:
    return _run_steps(
        [(["sudo", "-n", "systemctl", "reload", "nginx"], "RELOAD_NGINX")], _DEFAULT_TIMEOUT_SECONDS
    )


def backup_now() -> ExecutionResult:
    """调用既有 `scripts/backup_selfhosted_pg.py`，不重写备份逻辑（design.md `ops-backup`）。"""
    return _run_steps(
        [(["uv", "run", "python", "scripts/backup_selfhosted_pg.py"], "BACKUP")],
        _DEFAULT_TIMEOUT_SECONDS,
    )


@dataclass
class DeployPlan:
    target_sha: str
    with_migration: bool
    allowed_branch: str = "v2"


def _capture_current_sha() -> str | None:
    """发布前记下当前 checkout，供失败时回退（见 `deploy()` 说明）。探测失败返回
    `None`——调用方据此跳过回退，不能把它当某个具体 SHA 处理。
    """
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=".", capture_output=True, text=True, timeout=10, check=True
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return None
    return proc.stdout.strip() or None


def deploy(plan: DeployPlan) -> ExecutionResult:
    """检出确切 SHA 后调用 `deploy.sh`，不重写部署逻辑。

    迁移是否执行由 `plan.with_migration` 显式驱动（布尔字段，不是字符串拼接）——
    这个决定在飞书确认卡片阶段就已经展示给用户，执行器只是照办，不再重新判断
    （design.md D5）。`deploy.sh` 内部的 `read -r -p` 交互确认因此必须被
    `--yes` 跳过，否则会在一个没有 TTY 的子进程里永久挂起。

    **只在确定生产状态还没变时才回退工作树。** `GIT_FETCH`/`GIT_CHECKOUT` 失败发生在
    `deploy.sh` 被调用之前，此时容器与数据库都还没动，回退到发布前的 checkout 是安全的：
    否则下一次重试会把这个还没真正生效的新 SHA 误当成"已部署版本"，让
    `ops_bot.release.git_ref.migrations_changed`/`rollback_needs_review` 算出错误的
    迁移边界（PR #99 复审第三轮 P1）。

    **`DEPLOY_SH` 失败时禁止自动回退。** `deploy.sh` 内部先跑迁移、切前端产物、
    `docker compose up -d` 切容器，最后才做健康检查——它可能在**已经改动生产状态之后**
    才因为健康检查失败而以非零退出（`deploy.sh` 最后一段）。执行器只知道
    "`DEPLOY_SH` 这一步失败了"，无法区分失败发生在这一整段脚本的哪个子阶段，因此不能
    像对待 `GIT_CHECKOUT` 失败那样默认"生产状态没变"。若这里仍然无条件回退 checkout，
    容器与 schema 已经是新版本、但 HEAD 却回到了旧版本，本该由 HEAD 承担的
    "当前实际部署到哪个版本"这个事实来源就会被这次回退悄悄污染成假的旧版本
    ——比 PR #99 第三轮要修的"不回退"问题更隐蔽，因为它把回退动作本身变成了新的
    错误来源（PR #99 复审第四轮 P1）。这种情况下工作树保持在 `target_sha`，
    连带真实反映"这次发布可能已经部分生效，需要人工核实"，而不是给出一个看似
    干净、实则不可信的"已回退"假象。
    """
    previous_sha = _capture_current_sha()

    steps: list[tuple[list[str], str]] = [
        (["git", "fetch", "origin", plan.allowed_branch], "GIT_FETCH"),
        (["git", "checkout", plan.target_sha], "GIT_CHECKOUT"),
    ]
    deploy_cmd = ["./deploy.sh", "--yes"]
    if not plan.with_migration:
        deploy_cmd.append("--skip-migrate")
    steps.append((deploy_cmd, "DEPLOY_SH"))

    result = _run_steps(steps, _DEPLOY_TIMEOUT_SECONDS)
    if result.success or previous_sha is None:
        return result

    if result.failed_stage == "DEPLOY_SH":
        note = (
            "\n（未自动回退 checkout：deploy.sh 可能已经改动了生产状态，"
            "工作树保留在目标 SHA 以如实反映这一点，请人工核实容器与数据库的实际版本）"
        )
        return ExecutionResult(False, _truncate(result.output + note), result.failed_stage)

    restore = subprocess.run(
        ["git", "checkout", previous_sha], cwd=".", capture_output=True, text=True,
        timeout=_DEFAULT_TIMEOUT_SECONDS, check=False,
    )
    note = f"\n$ git checkout {previous_sha}（回退到发布前的版本）\n{restore.stdout}{restore.stderr}"
    return ExecutionResult(False, _truncate(result.output + note), result.failed_stage)
