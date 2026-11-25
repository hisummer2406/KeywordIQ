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

function scheduled({ event = 'pull_request', base = 'main', ref = 'refs/heads/main', draft = false,
                     result = 'success', outputs = {}, cancelled = false } = {}) {
  const github = { event_name: event, base_ref: base, ref, event: { pull_request: { draft } } };
  const needs = { changes: { result, outputs } };
  return Object.fromEntries(Object.entries(conditions).map(([job, expression]) => [
    job,
    // 用真实 YAML 内的表达式验证调度，避免仅测分类器却遗漏 needs/草稿条件。
    new Function('github', 'needs', 'always', 'cancelled', `return (${expression});`)(
      github, needs, () => true, () => cancelled,
    ),
  ]));
}


test('main push 与 PR 按范围调度', () => {
  for (const event of ['push', 'pull_request']) {
    assert.deepEqual(scheduled({ event, outputs: { backend: 'false', frontend: 'true', extension: 'false' } }), scope(false, true, false));
  }
});
test('CI 只接受 main push 与面向 main 的 PR', () => {
  assert.deepEqual([...workflow.matchAll(/branches: \[([^\]]+)\]/g)].map(match => match[1]), ['main', 'main']);
  assert.match(workflow, /github.ref == 'refs\/heads\/main'.*github.base_ref == 'main'/);
});
test('草稿不运行；判定失败或缺失时全量；取消后不再启动', () => {
  assert.deepEqual(scheduled({ draft: true }), scope(false, false, false));
  for (const result of ['failure', 'skipped', 'cancelled', 'success']) assert.deepEqual(scheduled({ result }), allChecks());
  assert.deepEqual(scheduled({ cancelled: true }), scope(false, false, false));
});
test('纯文档 push 跳过三项测试', () => {
  assert.deepEqual(scheduled({ event: 'push', outputs: { backend: 'false', frontend: 'false', extension: 'false' } }), scope(false, false, false));
});
test('汇总门禁始终核对同一 push 的全部依赖', () => {
  assert.match(workflow, /needs: \[changes, backend, frontend, extension\]/);
  assert.match(workflow, /if: always\(\) && !cancelled\(\) && github.event_name == 'push' && github.ref == 'refs\/heads\/main'/);
  assert.match(workflow, /CI_NEEDS: \$\{\{ toJSON\(needs\) \}\}/);
});
