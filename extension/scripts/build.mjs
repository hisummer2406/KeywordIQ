// 构建脚本：esbuild 把三个入口各打成一个独立 IIFE，再把静态文件拷进 dist/。
// 不用 vite/@crxjs 这类专用 MV3 插件——三个自包含 IIFE 已经够用，esbuild
// 零配置成本更低、依赖面更小（design.md/tasks.md 1.2）。
import { build } from 'esbuild';
import { cp, mkdir, rm } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const outdir = path.join(root, 'dist');

await rm(outdir, { recursive: true, force: true });
await mkdir(outdir, { recursive: true });

await build({
  entryPoints: [
    path.join(root, 'src/background.ts'),
    path.join(root, 'src/content-script.ts'),
    path.join(root, 'src/options.ts'),
  ],
  bundle: true,
  format: 'iife',
  target: 'chrome110',
  outdir,
  sourcemap: true,
});

await cp(path.join(root, 'manifest.json'), path.join(outdir, 'manifest.json'));
await cp(path.join(root, 'options.html'), path.join(outdir, 'options.html'));
await cp(path.join(root, 'icons'), path.join(outdir, 'icons'), { recursive: true });

console.log(`built -> ${outdir}`);
