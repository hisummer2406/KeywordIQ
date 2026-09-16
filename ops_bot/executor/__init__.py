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


def deploy(plan: DeployPlan) -> ExecutionResult:
    """检出确切 SHA 后调用 `deploy.sh`，不重写部署逻辑。

    迁移是否执行由 `plan.with_migration` 显式驱动（布尔字段，不是字符串拼接）——
    这个决定在飞书确认卡片阶段就已经展示给用户，执行器只是照办，不再重新判断
    （design.md D5）。`deploy.sh` 内部的 `read -r -p` 交互确认因此必须被
    `--yes` 跳过，否则会在一个没有 TTY 的子进程里永久挂起。
    """
    steps: list[tuple[list[str], str]] = [
        (["git", "fetch", "origin", plan.allowed_branch], "GIT_FETCH"),
        (["git", "checkout", plan.target_sha], "GIT_CHECKOUT"),
    ]
    deploy_cmd = ["./deploy.sh", "--yes"]
    if not plan.with_migration:
        deploy_cmd.append("--skip-migrate")
    steps.append((deploy_cmd, "DEPLOY_SH"))
    return _run_steps(steps, _DEPLOY_TIMEOUT_SECONDS)
