// Compare API 最多返回 300 个文件；达到上限、缺失或空结果都保守跑全套。
const allChecks = () => ({ backend: true, frontend: true, extension: true });

function selectChecks(files) {
  if (!Array.isArray(files) || files.length === 0 || files.length >= 300) {
    return allChecks();
  }

  const checks = { backend: false, frontend: false, extension: false };
  for (const file of files) {
    if (!file || typeof file.filename !== 'string' || !file.filename ||
        (file.status === 'renamed' && !file.previous_filename)) {
      return allChecks();
    }
    // 跨目录重命名同时影响原位置和新位置；删除文件同样必须检查。
    for (const path of [file.filename, file.previous_filename].filter(Boolean)) {
      if (path.startsWith('frontend/')) {
        checks.frontend = true;
      } else if (path.startsWith('extension/')) {
        checks.extension = true;
      } else if (/^(app|ops_bot|alembic|test)\//.test(path) ||
                 /^[^/]+\.py$/.test(path) ||
                 ['pyproject.toml', 'uv.lock', '.python-version', 'alembic.ini', 'pytest.ini'].includes(path)) {
        checks.backend = true;
      } else if (/^(docs|openspec)\//.test(path) ||
                 ['README.md', 'CLAUDE.md', 'AGENTS.md', 'LICENSE'].includes(path)) {
        // 纯文档 PR 只执行本 job，三个必需 job 用条件跳过，避免一直 Pending。
      } else {
        // CI、scripts、公共配置及新增的未知目录，不能猜测其影响面。
        return allChecks();
      }
    }
  }
  return checks;
}

module.exports = { selectChecks, allChecks };
