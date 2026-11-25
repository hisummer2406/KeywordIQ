
const assert = require('node:assert/strict');
const { test } = require('node:test');
const { names, READY, verifiedJobs, validateNeeds, resolveScope, verifyRelease } = require('./ci-evidence.cjs');
const { allChecks } = require('./ci-scope.cjs');
const job = (name, conclusion = 'success') => ({ name, status: 'completed', conclusion });
const legacy = () => Object.values(names).map(name => job(name));
const scoped = () => [job(READY), ...Object.values(names).map(name => job(name, 'skipped'))];
const run = (id = 1, head_sha = 'base') => ({ id, head_sha, event: 'push', head_branch: 'main', path: '.github/workflows/ci.yml', status: 'completed', conclusion: 'success' });
const repo = { owner: 'owner', repo: 'repo' };
function harness({ runs = [run()], jobs = [job(READY), ...legacy()], files = [{ filename: 'frontend/a.ts' }], status = 'ahead', merge = 'base', error = false, eventName = 'push', ref = 'refs/heads/main' } = {}) {
  const calls = [];
  const github = { rest: {
    actions: {
      listWorkflowRuns: async args => { calls.push(args); if (error) throw Error('API unavailable'); return { data: { workflow_runs: runs } }; },
      listJobsForWorkflowRun: () => {},
    },
    repos: { compareCommitsWithBasehead: async args => { calls.push(args); return { data: { files, status, merge_base_commit: { sha: merge } } }; } },
  }, paginate: async (_method, args) => { calls.push(args); return jobs; } };
  const context = { repo, sha: 'head', eventName, ref, payload: eventName === 'pull_request' ? { pull_request: { base: { sha: 'pr-base' }, head: { sha: 'pr-head' } } } : {} };
  const warnings = [];
  const core = { info() {}, warning(message) { warnings.push(message); } };
  return { github, context, core, calls, warnings };
}
test('新 main 要求发布就绪门禁，即使旧 main 曾全量成功', () => {
  assert.equal(verifiedJobs(legacy()), false);
  assert.equal(verifiedJobs([job(READY), ...legacy()]), true);
  for (const conclusion of ['skipped', 'failure', 'cancelled', null]) {
    const jobs = legacy(); jobs[0].conclusion = conclusion; assert.equal(verifiedJobs(jobs), false);
  }
});
test('新版成功门禁允许无关项跳过，缺失或重复仍拦截', () => {
  assert.equal(verifiedJobs(scoped()), true);
  assert.equal(verifiedJobs(scoped().slice(0, 3)), false);
  assert.equal(verifiedJobs([...scoped(), job(names.backend)]), false);
  const jobs = scoped(); jobs[0].conclusion = 'skipped'; assert.equal(verifiedJobs(jobs), false);
});
test('汇总门禁逐项核对范围与结果，失败、取消、漏跑均拒绝', () => {
  const needs = { changes: { result: 'success', outputs: { backend: 'true', frontend: 'false', extension: 'false' } }, backend: { result: 'success' }, frontend: { result: 'skipped' }, extension: { result: 'skipped' } };
  assert.doesNotThrow(() => validateNeeds(needs));
  for (const result of ['skipped', 'failure', 'cancelled', undefined]) {
    assert.throws(() => validateNeeds({ ...needs, backend: { result } }));
  }
  assert.throws(() => validateNeeds({ ...needs, changes: { result: 'failure' } }));
  assert.throws(() => validateNeeds({ ...needs, changes: { result: 'success', outputs: {} } }));
});
test('push 从已验证基线累计比较，包含此前失败或取消的改动', async () => {
  const h = harness({ files: [{ filename: 'app/a.py' }, { filename: 'frontend/a.ts' }] });
  assert.deepEqual(await resolveScope(h), { backend: true, frontend: true, extension: false });
  assert.equal(h.calls[0].event, 'push');
  assert.equal(h.calls[0].branch, 'main');
  assert.equal(h.calls[0].status, 'success');
  assert.equal(h.calls[2].basehead, 'base...head');
});
test('新版汇总通过的祖先可作为下一轮基线', async () => {
  assert.deepEqual(await resolveScope(harness({ jobs: scoped() })), { backend: false, frontend: true, extension: false });
});
test('找不到基线、API 失败、历史分叉、未验证基线均全量', async () => {
  for (const options of [{ runs: [] }, { error: true }, { status: 'diverged' }, { merge: 'other' }, { jobs: [] }, { jobs: legacy() }, { runs: [{ ...run(), head_branch: 'v2' }] }, { runs: [ { ...run(), event: 'pull_request' } ] }]) {
    assert.deepEqual(await resolveScope(harness(options)), allChecks());
  }
});
test('比较被截断也全量', async () => {
  assert.deepEqual(await resolveScope(harness({ files: Array(300).fill({ filename: 'frontend/a.ts' }) })), allChecks());
});
test('PR 仍比较固定 base/head，不继承 push 基线', async () => {
  const h = harness({ eventName: 'pull_request' });
  await resolveScope(h);
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].basehead, 'pr-base...pr-head');
});
test('发布只接受目标 SHA 的最新合格 main push CI，同一运行分页取齐 jobs', async () => {
  const h = harness({ eventName: 'workflow_dispatch', runs: [run(2, 'head')], jobs: scoped() });
  assert.equal(await verifyRelease(h), 2);
  assert.equal(h.calls[0].head_sha, 'head');
  assert.equal(h.calls[0].branch, 'main');
  assert.equal(h.calls[1].run_id, 2);
  assert.equal(h.calls[1].filter, 'latest');
});
test('PR CI、旧分支、其他 SHA、缺失或失败的证据均拒绝发布', async () => {
  const options = { eventName: 'workflow_dispatch' };
  for (const bad of [
    { event: 'pull_request' }, { head_sha: 'other' }, { path: '.github/workflows/other.yml' },
    { head_branch: 'v2' }, { status: 'in_progress' }, { conclusion: 'cancelled' }, { conclusion: 'failure' },
  ]) await assert.rejects(verifyRelease(harness({ ...options, runs: [{ ...run(2, 'head'), ...bad }] })));
  for (const config of [
    { runs: [] },
    { runs: [run(1, 'head'), { ...run(2, 'head'), conclusion: 'failure' }] },
    { runs: [run(2, 'head')], jobs: [] },
    { runs: [run(2, 'head')], jobs: legacy() },
  ]) await assert.rejects(verifyRelease(harness({ ...options, ...config })));
});

test('非 main 或非手动触发不查询发布证据', async () => {
  for (const options of [{ eventName: 'push' }, { eventName: 'workflow_dispatch', ref: 'refs/heads/v1' }]) {
    const h = harness(options);
    await assert.rejects(verifyRelease(h));
    assert.equal(h.calls.length, 0);
  }
});
