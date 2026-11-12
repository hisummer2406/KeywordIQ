const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { selectChecks, allChecks } = require('./ci-scope.cjs');

const scope = (backend, frontend, extension) => ({ backend, frontend, extension });
const files = (...paths) => paths.map(filename => ({ filename }));

for (const [name, input, expected] of [
  ['前端改动', files('frontend/src/pages/Home.tsx'), scope(false, true, false)],
  ['前端锁文件', files('frontend/pnpm-lock.yaml'), scope(false, true, false)],
  ['插件改动', files('extension/manifest.json'), scope(false, false, true)],
  ['后端改动', files('app/main.py', 'ops_bot/config.py'), scope(true, false, false)],
  ['迁移与测试', files('alembic/versions/example.py', 'test/test_example.py'), scope(true, false, false)],
  ['后端依赖', files('uv.lock', 'pyproject.toml', '.python-version'), scope(true, false, false)],
  ['根模块', files('config.py'), scope(true, false, false)],
  ['文档', files('docs/ops/example.md', 'openspec/specs/example/spec.md', 'README.md'), scope(false, false, false)],
  ['混合改动', files('frontend/src/app.tsx', 'app/main.py'), scope(true, true, false)],
  ['CI 配置', files('.github/workflows/ci.yml'), allChecks()],
  ['公共脚本', files('scripts/make_pwa_icons.py'), allChecks()],
  ['未知文件', files('shared/contracts.json'), allChecks()],
  ['缺失文件列表', undefined, allChecks()],
  ['空比较结果', [], allChecks()],
  ['API 达到 300 文件上限', Array(300).fill({ filename: 'frontend/src/app.tsx' }), allChecks()],
  ['无效 API 结果', [{}], allChecks()],
  ['重命名缺失原路径', [{ filename: 'docs/config.md', status: 'renamed' }], allChecks()],
  ['跨域重命名', [{ filename: 'frontend/a.json', previous_filename: 'extension/a.json', status: 'renamed' }], scope(false, true, true)],
  ['从后端移到文档', [{ filename: 'docs/config.py', previous_filename: 'app/config.py', status: 'renamed' }], scope(true, false, false)],
  ['删除文件', [{ filename: 'app/old.py', status: 'removed' }], scope(true, false, false)],
]) {
  test(name, () => assert.deepEqual(selectChecks(input), expected));
}

const workflow = readFileSync(join(__dirname, 'workflows/ci.yml'), 'utf8');
const conditions = {};
for (const job of ['backend', 'frontend', 'extension']) {
  const block = workflow.split(`\n  ${job}:\n`)[1].split(/\n  (?=\S)/)[0];
  assert.match(block, /needs: changes/);
  conditions[job] = block.match(/    if: >-\n([\s\S]*?)    runs-on:/)[1].trim();
}

function scheduled({ event = 'pull_request', base = 'v2', draft = false,
                     result = 'success', outputs = {}, cancelled = false } = {}) {
  const github = { event_name: event, base_ref: base, event: { pull_request: { draft } } };
  const needs = { changes: { result, outputs } };
  return Object.fromEntries(Object.entries(conditions).map(([job, expression]) => [
    job,
    // 用真实 YAML 内的表达式验证调度，避免仅测分类器却遗漏 needs/草稿条件。
    new Function('github', 'needs', 'always', 'cancelled', `return (${expression});`)(
      github, needs, () => true, () => cancelled,
    ),
  ]));
}

test('v2 push 不受 skipped 的判定 job 影响，仍全量', () => {
  assert.deepEqual(scheduled({ event: 'push', result: 'skipped' }), allChecks());
});
test('main push 与 PR 保留全量检查', () => {
  assert.deepEqual(scheduled({ event: 'push', base: '', result: 'skipped' }), allChecks());
  assert.deepEqual(scheduled({ base: 'main', draft: true, result: 'skipped' }), allChecks());
});
test('v2 草稿不运行三项检查', () => {
  assert.deepEqual(scheduled({ draft: true, result: 'skipped' }), scope(false, false, false));
});
test('正式 PR 只运行选中的检查', () => {
  assert.deepEqual(scheduled({ outputs: { backend: 'false', frontend: 'true', extension: 'false' } }), scope(false, true, false));
});
test('纯文档 PR 跳过三项检查', () => {
  assert.deepEqual(scheduled({ outputs: { backend: 'false', frontend: 'false', extension: 'false' } }), scope(false, false, false));
});
test('判定失败、跳过或输出缺失时全量', () => {
  for (const result of ['failure', 'skipped', 'cancelled', 'success']) {
    assert.deepEqual(scheduled({ result }), allChecks());
  }
});
test('新提交取消旧运行后不会继续启动检查', () => {
  assert.deepEqual(scheduled({ event: 'push', cancelled: true }), scope(false, false, false));
});
test('草稿与正式状态切换均触发，保证 Ready 后运行、转回草稿时取消旧任务', () => {
  assert.match(workflow, /types: \[opened, synchronize, reopened, ready_for_review, converted_to_draft\]/);
  assert.match(workflow, /if: github\.event_name == 'pull_request' && github\.base_ref == 'v2' && !github\.event\.pull_request\.draft/);
  assert.match(workflow, /cancel-in-progress: true/);
  assert.match(workflow, /basehead: `\$\{pr\.base\.sha\}\.\.\.\$\{pr\.head\.sha\}`/);
});

const script = workflow.split('          script: |\n')[1]
  .split(/\n(?=\S|  \S)/)[0].replace(/^            /gm, '');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

async function githubScript({ response, error } = {}) {
  const outputs = {};
  const warnings = [];
  const expected = { owner: 'owner', repo: 'repo', basehead: 'base-sha...head-sha', per_page: 1 };
  const context = {
    repo: { owner: 'owner', repo: 'repo' },
    payload: { pull_request: { base: { sha: 'base-sha' }, head: { sha: 'head-sha' } } },
  };
  const github = { rest: { repos: { compareCommitsWithBasehead: async args => {
    assert.deepEqual(args, expected);
    if (error) throw error;
    return { data: response };
  } } } };
  const core = {
    warning: message => warnings.push(message),
    info: () => {},
    setOutput: (name, value) => { outputs[name] = value; },
  };
  // 实际 workflow script 的 require 路径从仓库根解析。
  await new AsyncFunction('require', 'context', 'github', 'core', script)(
    path => { assert.equal(path, './.github/ci-scope.cjs'); return { selectChecks, allChecks }; },
    context, github, core,
  );
  return { outputs, warnings };
}

test('真实 workflow script 用固定 SHA 比较并输出字符串，正确接入调度条件', async () => {
  const { outputs, warnings } = await githubScript({ response: { files: files('frontend/src/app.tsx') } });
  assert.deepEqual(outputs, { backend: 'false', frontend: 'true', extension: 'false' });
  assert.deepEqual(scheduled({ outputs }), scope(false, true, false));
  assert.deepEqual(warnings, []);
});
test('真实 workflow script 在 API 错误时回退全套', async () => {
  const { outputs, warnings } = await githubScript({ error: new Error('API unavailable') });
  assert.deepEqual(scheduled({ outputs }), allChecks());
  assert.equal(warnings.length, 1);
});
test('真实 workflow script 对缺失文件和达到截断上限都全量', async () => {
  for (const response of [{}, { files: Array(300).fill({ filename: 'frontend/a.ts' }) }]) {
    const { outputs } = await githubScript({ response });
    assert.deepEqual(scheduled({ outputs }), allChecks());
  }
});
