// 打包脚本：先构建，再把 dist/ 压成带版本号的 zip，放进 release/。
// 分发方式是「zip + 加载已解压」（见 docs/extension-install.md），所以产物就是
// dist/ 本身；sourcemap 只服务本地调试，不进分发包。
import { execFileSync } from 'node:child_process';
import { mkdir, readFile, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const dist = path.join(root, 'dist');
const releaseDir = path.join(root, 'release');

const pkg = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
const manifest = JSON.parse(await readFile(path.join(root, 'manifest.json'), 'utf8'));

// 版本只该有一个事实来源；两处不一致时打出来的包版本号会撒谎
if (pkg.version !== manifest.version) {
  throw new Error(`package.json (${pkg.version}) 与 manifest.json (${manifest.version}) 版本不一致`);
}
// 没有 key 字段，扩展 ID 会随解压路径变化，运营挪一下文件夹设置与 host 权限就丢
if (typeof manifest.key !== 'string' || manifest.key.length === 0) {
  throw new Error('manifest.json 缺少 key 字段，扩展 ID 将不固定');
}

execFileSync('node', [path.join(root, 'scripts/build.mjs')], { stdio: 'inherit' });

const zipName = `keywordiq-competitor-collector-v${manifest.version}.zip`;
const zipPath = path.join(releaseDir, zipName);
await mkdir(releaseDir, { recursive: true });
await rm(zipPath, { force: true });

// -X 去掉额外文件属性，让同一份 dist 重复打包的结果内容一致
execFileSync('zip', ['-r', '-X', '-q', zipPath, '.', '-x', '*.map', '.DS_Store'], { cwd: dist });

console.log(`packed -> ${zipPath}`);
