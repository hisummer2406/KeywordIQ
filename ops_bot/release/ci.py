"""发布 CI 校验：全量成功，或同一运行的按范围汇总门禁成功。"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from config import settings

# GitHub 的 check-runs API 按 job 的 `name:` 显示名索引，不是 job id——
# `.github/workflows/ci.yml` 里 job id 是 `backend`/`frontend`/`extension`，
# 但各自的 `name:` 是中文展示名。用 job id 在这里查询永远查不到，
# `check()` 会把全部三项都判成"未找到"，导致 CI 全绿时发布仍然被拒
# （PR #99 Codex 复审 P1）。这三个字符串必须与 ci.yml 对应 job 的 `name:`
# 逐字一致，改工作流的 job 名要同步改这里。
REQUIRED_CHECK_NAMES = (
    "后端（pytest + alembic）",
    "前端（lint + tsc + test + build）",
    "竞对采集插件（typecheck + test + build）",
)
READY_CHECK_NAME = "发布就绪（按范围校验）"
_TIMEOUT = 10.0


@dataclass
class CICheckResult:
    passing: bool
    details: dict[str, str] = field(default_factory=dict)
    """job 名 → 人类可读状态，供发布确认卡片展示。"""


async def check(sha: str) -> CICheckResult:
    if not settings.GH_TOKEN:
        return CICheckResult(passing=False, details={"_error": "GH_TOKEN 未配置，无法核实 CI"})

    url = f"https://api.github.com/repos/{settings.GH_REPO}/commits/{sha}/check-runs"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, trust_env=False) as client:
            response = await client.get(
                url,
                headers={
                    "Authorization": f"Bearer {settings.GH_TOKEN}",
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100},
            )
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError as exc:
        return CICheckResult(passing=False, details={"_error": f"查询 CI 状态失败：{exc}"})

    if data.get("total_count", len(data.get("check_runs", []))) > len(data.get("check_runs", [])):
        return CICheckResult(passing=False, details={"_error": "CI 结果不完整，无法核实"})

    # 同一 SHA 可能因重跑产生多条同名记录，取 started_at 最新的一条——不是"要不要
    # 认过去的失败"这类判断，纯粹是"这个 job 现在到底是什么状态"只有一个答案。
    latest_by_name: dict[str, dict] = {}
    for run in data.get("check_runs", []):
        name = run.get("name")
        if not name:
            continue
        existing = latest_by_name.get(name)
        if existing is None or (run.get("started_at") or "") > (existing.get("started_at") or ""):
            latest_by_name[name] = run

    ready = latest_by_name.get(READY_CHECK_NAME)
    scoped = False
    if ready is not None:
        suite_id = (ready.get("check_suite") or {}).get("id")
        if (
            ready.get("status") != "completed"
            or ready.get("conclusion") != "success"
            or ready.get("head_sha") != sha
            or (ready.get("app") or {}).get("slug") != "github-actions"
            or suite_id is None
        ):
            return CICheckResult(passing=False, details={"_error": "按范围发布门禁未通过"})
        latest_by_name = {
            name: run for name, run in latest_by_name.items()
            if (run.get("check_suite") or {}).get("id") == suite_id
        }
        scoped = True

    details: dict[str, str] = {}
    passing = True
    for name in REQUIRED_CHECK_NAMES:
        run = latest_by_name.get(name)
        if run is None:
            details[name] = "未找到（无法核实）"
            passing = False
            continue
        status = run.get("status")
        conclusion = run.get("conclusion")
        if status != "completed":
            details[name] = f"进行中（{status}）"
            passing = False
        elif scoped and conclusion == "skipped":
            details[name] = "未受影响（已由发布门禁核验）"
        elif conclusion != "success":
            details[name] = f"失败（{conclusion}）"
            passing = False
        else:
            details[name] = "成功"

    return CICheckResult(passing=passing, details=details)
