const { selectChecks, allChecks } = require('./ci-scope.cjs');

const names = {
  backend: '后端（pytest + alembic）',
  frontend: '前端（lint + tsc + test + build）',
  extension: '竞对采集插件（typecheck + test + build）',
};
const READY = '发布就绪（按范围校验）';
const passed = job => job?.status === 'completed' && job.conclusion === 'success';

function verifiedJobs(jobs) {
  const ready = jobs.filter(job => job.name === READY);
  const checks = Object.values(names).map(name => jobs.filter(job => job.name === name));
  if (checks.some(matches => matches.length !== 1)) return false;
  // 新策略只认同一运行中的汇总门禁；旧版本仅接受三项全部成功。
  if (ready.length) {
    return ready.length === 1 && passed(ready[0]) && checks.every(([job]) =>
      job.status === 'completed' && ['success', 'skipped'].includes(job.conclusion));
  }
  return checks.every(([job]) => passed(job));
}

function validateNeeds(needs) {
  if (needs.changes?.result !== 'success') throw new Error('检查范围未验证');
  for (const key of Object.keys(names)) {
    const selected = needs.changes.outputs[key];
    if (!['true', 'false'].includes(selected)) throw new Error(`范围缺失：${key}`);
    const expected = selected === 'true' ? 'success' : 'skipped';
    if (needs[key]?.result !== expected) throw new Error(`检查未满足范围策略：${key}`);
  }
}

async function jobsFor(github, repo, run) {
  return github.paginate(github.rest.actions.listJobsForWorkflowRun, {
    ...repo, run_id: run.id, filter: 'latest', per_page: 100,
  });
}
const isPushCI = run => run.event === 'push' && run.head_branch === 'v2' &&
  run.path === '.github/workflows/ci.yml';

async function resolveScope({ github, context, core }) {
  try {
    let base;
    const head = context.payload.pull_request?.head.sha || context.sha;
    if (context.eventName === 'pull_request') {
      base = context.payload.pull_request.base.sha;
    } else {
      // 不能只看 push.before：之前失败或被取消的提交也必须累计检查。
      const { data } = await github.rest.actions.listWorkflowRuns({
        ...context.repo, workflow_id: 'ci.yml', branch: 'v2', event: 'push',
        status: 'success', per_page: 30,
      });
      for (const run of data.workflow_runs) {
        if (!isPushCI(run) || run.head_sha === head || run.status !== 'completed' ||
            run.conclusion !== 'success' || !verifiedJobs(await jobsFor(github, context.repo, run))) continue;
        const { data: comparison } = await github.rest.repos.compareCommitsWithBasehead({
          ...context.repo, basehead: `${run.head_sha}...${head}`, per_page: 1,
        });
        if (comparison.status === 'ahead' && comparison.merge_base_commit?.sha === run.head_sha) {
          core.info(`累计检查基线：${run.head_sha}，CI run ${run.id}`);
          return selectChecks(comparison.files);
        }
      }
      throw new Error('未找到已验证且位于目标历史中的 CI 基线');
    }
    const { data } = await github.rest.repos.compareCommitsWithBasehead({
      ...context.repo, basehead: `${base}...${head}`, per_page: 1,
    });
    return selectChecks(data.files);
  } catch (error) {
    core.warning(`无法确认改动范围，运行全部检查：${error.message}`);
    return allChecks();
  }
}

async function verifyRelease({ github, context }) {
  const { data } = await github.rest.actions.listWorkflowRuns({
    ...context.repo, workflow_id: 'ci.yml', branch: 'v2', event: 'push',
    head_sha: context.sha, per_page: 100,
  });
  // 只核对本仓 v2 push 的最新运行，不混用 PR、其他 SHA 或旧重跑结果。
  const run = data.workflow_runs.filter(run => isPushCI(run) && run.head_sha === context.sha)
    .sort((a, b) => b.id - a.id || b.run_attempt - a.run_attempt)[0];
  if (!run || run.status !== 'completed' || run.conclusion !== 'success' ||
      !verifiedJobs(await jobsFor(github, context.repo, run))) {
    throw new Error('目标 SHA 的 v2 CI 尚未验证通过');
  }
  return run.id;
}

module.exports = { names, READY, verifiedJobs, validateNeeds, resolveScope, verifyRelease };
