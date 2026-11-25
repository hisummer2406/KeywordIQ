const assert = require('node:assert/strict');
const { execFileSync, spawnSync } = require('node:child_process');
const { mkdtempSync, readFileSync, rmSync, writeFileSync } = require('node:fs');
const { tmpdir } = require('node:os');
const { join } = require('node:path');
const { test } = require('node:test');

const workflow = readFileSync(join(__dirname, 'workflows/deploy-v2.yml'), 'utf8');
const remoteScript = workflow.split("<<'REMOTE'\n")[1].split('\n          REMOTE')[0]
  .replace(/^ {10}/gm, '');
const checkoutScript = remoteScript.split('\nsudo -n ')[0]
  .replace('cd /var/www/keywordiq', 'cd "$TEST_REPO"');

function fixture(t) {
  const root = mkdtempSync(join(tmpdir(), 'keywordiq-deploy-test-'));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const upstream = join(root, 'upstream');
  const ecs = join(root, 'ecs');
  const git = (cwd, ...args) => execFileSync('git', args, {
    cwd, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
  }).trim();

  git(root, 'init', '-q', '-b', 'v2', upstream);
  git(upstream, 'config', 'user.name', 'CI');
  git(upstream, 'config', 'user.email', 'ci@example.invalid');
  const commit = value => {
    writeFileSync(join(upstream, 'version'), value);
    git(upstream, 'add', 'version');
    git(upstream, 'commit', '-qm', value);
    return git(upstream, 'rev-parse', 'HEAD');
  };
  const original = commit('V2 running');
  git(root, 'clone', '-q', '--single-branch', '-b', 'v2', upstream, ecs);
  git(upstream, 'switch', '-qc', 'main');
  const candidate = commit('V2 on main');
  git(upstream, 'switch', '-qc', 'v1', original);
  const archived = commit('V1 archive');

  const deploy = (sha = candidate, ref = 'refs/heads/main') => spawnSync('bash', ['-e', '-s'], {
    input: checkoutScript,
    encoding: 'utf8',
    env: { ...process.env, TEST_REPO: ecs, DEPLOY_REF: ref, DEPLOY_SHA: sha },
  });
  return {
    ecs, deploy, original, candidate, archived,
    git: (...args) => git(ecs, ...args),
  };
}

test('发布 job 只接受 main，并在准备 SSH 前核对 CI', () => {
  assert.match(workflow, /if: github.ref == 'refs\/heads\/main'/);
  assert.ok(workflow.indexOf('await verifyRelease(') < workflow.indexOf('配置 SSH 连接'));
});

test('ECS 仅跟踪 v2 时也能快进到 main，保留原 v2 引用', t => {
  const { deploy, git, original, candidate } = fixture(t);
  assert.equal(git('config', '--get-all', 'remote.origin.fetch'), '+refs/heads/v2:refs/remotes/origin/v2');
  const result = deploy();
  assert.equal(result.status, 0, result.stderr);
  assert.equal(git('branch', '--show-current'), 'main');
  assert.equal(git('rev-parse', 'HEAD'), candidate);
  assert.equal(git('rev-parse', 'refs/heads/v2'), original);
  assert.equal(deploy().status, 0);
  assert.match(deploy(original).stderr, /无法快进/);
  assert.equal(git('rev-parse', 'HEAD'), candidate);
});

test('ECS 拒绝错误分支、短 SHA 和 main 历史外的提交', t => {
  const { deploy, git, original, archived } = fixture(t);
  assert.match(deploy(undefined, 'refs/heads/v1').stderr, /拒绝非 main/);
  assert.match(deploy(archived.slice(0, 7)).stderr, /完整的提交 SHA/);
  assert.match(deploy(archived).stderr, /不在 origin\/main 历史/);
  assert.equal(git('branch', '--show-current'), 'v2');
  assert.equal(git('rev-parse', 'HEAD'), original);
});

test('ECS 拒绝已有不同的本地 main 和已跟踪修改', t => {
  const { ecs, deploy, git, original, archived } = fixture(t);
  git('fetch', '-q', 'origin', 'refs/heads/v1:refs/heads/main');
  assert.match(deploy().stderr, /已有不同的本地 main/);
  assert.equal(git('rev-parse', 'refs/heads/main'), archived);
  writeFileSync(join(ecs, 'version'), 'local change');
  assert.match(deploy().stderr, /已跟踪的本地修改/);
  git('add', 'version');
  assert.match(deploy().stderr, /已跟踪的本地修改/);
  assert.equal(git('branch', '--show-current'), 'v2');
  assert.equal(git('rev-parse', 'HEAD'), original);
});
