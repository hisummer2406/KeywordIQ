import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * 可安装网站应用的静态契约（issue #92，openspec installable-web-app）。
 *
 * 这些东西**没有任何地方会在运行时报错**：Manifest 里图标路径写错，浏览器只是不提供
 * 安装；`<link>` 写成相对路径，深链接下会拿到 index.html 当 Manifest，同样只是静默不装。
 * 所以要在这里直接读产物文件断言，而不是等真机发现「安装按钮没出来」。
 */

// 不用 import.meta.url：测试环境是 jsdom，它在那里是 http://localhost 而不是 file://，
// fileURLToPath 会直接抛错。__dirname / __filename 由 vite-node 注入，与环境无关。
const FRONTEND = join(__dirname, '..');
const PUBLIC = join(FRONTEND, 'public');
const SRC = join(FRONTEND, 'src');

interface ManifestIcon {
  src: string;
  sizes: string;
  type: string;
  purpose?: string;
}
interface Manifest {
  id: string;
  name: string;
  short_name: string;
  start_url: string;
  scope: string;
  display: string;
  theme_color: string;
  background_color: string;
  icons: ManifestIcon[];
}

const manifest: Manifest = JSON.parse(readFileSync(join(PUBLIC, 'manifest.webmanifest'), 'utf8'));

/** 读 PNG 的 IHDR：8 字节签名 + 4 字节长度 + 4 字节 'IHDR' 之后是 4 字节宽、4 字节高。 */
function pngSize(path: string): { width: number; height: number } {
  const buf = readFileSync(path);
  expect(buf.subarray(1, 4).toString('ascii'), `${path} 不是 PNG`).toBe('PNG');
  return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20) };
}

/** 递归列文件。跳过 Umi 生成物（.umi / .umi-production）：它们被 gitignore，内容随 Umi 版本变。 */
function walk(dir: string): string[] {
  return readdirSync(dir)
    .filter((name) => !name.startsWith('.umi'))
    .flatMap((name) => {
      const full = join(dir, name);
      return statSync(full).isDirectory() ? walk(full) : [full];
    });
}

describe('Web App Manifest', () => {
  it('名称与界面既有产品名一致，独立窗口展示', () => {
    expect(manifest.name).toBe('海眸系统');
    expect(manifest.short_name).toBe('海眸系统');
    expect(manifest.display).toBe('standalone');
  });

  it('id 固定为站点根：改了会让已安装用户得到第二个应用', () => {
    expect(manifest.id).toBe('/');
  });

  it('start_url / scope / 图标路径都是相对路径，不硬编码根之外的前缀', () => {
    expect(manifest.start_url.startsWith('/')).toBe(false);
    expect(manifest.scope.startsWith('/')).toBe(false);
    for (const icon of manifest.icons) {
      expect(icon.src.startsWith('/')).toBe(false);
    }
  });

  it('声明 192 / 512 的 any 图标和 512 的 maskable 图标', () => {
    const has = (sizes: string, purpose: string) =>
      manifest.icons.some((i) => i.sizes === sizes && (i.purpose ?? 'any') === purpose);
    expect(has('192x192', 'any')).toBe(true);
    expect(has('512x512', 'any')).toBe(true);
    expect(has('512x512', 'maskable')).toBe(true);
  });

  it('每个图标文件真实存在，且像素尺寸与声明一致', () => {
    for (const icon of manifest.icons) {
      const [w, h] = icon.sizes.split('x').map(Number);
      expect(pngSize(join(PUBLIC, icon.src)), icon.src).toEqual({ width: w, height: h });
    }
  });

  it('提供 180×180 的 Apple touch icon', () => {
    expect(pngSize(join(PUBLIC, 'apple-touch-icon.png'))).toEqual({ width: 180, height: 180 });
  });
});

describe('入口页引用', () => {
  const umirc = readFileSync(join(FRONTEND, '.umirc.ts'), 'utf8');

  it('manifest 与 apple-touch-icon 的 href 以 / 开头', () => {
    // 相对路径在 /analysis 这类深链接下会解析到 /analysis/manifest.webmanifest，
    // 被 nginx 的 SPA 兜底当成 index.html 返回
    expect(umirc).toMatch(/rel:\s*'manifest',\s*href:\s*'\/manifest\.webmanifest'/);
    expect(umirc).toMatch(/rel:\s*'apple-touch-icon',\s*href:\s*'\/apple-touch-icon\.png'/);
  });
});

/**
 * 不引入离线能力（openspec installable-web-app「不引入离线能力」）。
 *
 * 一旦有 Service Worker，「发布后用户拿到哪一版」就多了一层不受服务端控制的缓存，而它
 * 的错误表现是用户长期停在旧版本、服务端毫无信号。本产品的数据新鲜度是核心语义。
 * 将来若真要做离线，应当是一个新的、经过评估的变更，改掉这条测试是那次评审的一部分。
 */
describe('没有 Service Worker', () => {
  const files = [...walk(PUBLIC), ...walk(SRC)];

  it('public/ 与 src/ 下没有 Service Worker 脚本', () => {
    const offenders = files.filter((f) => /(^|\/)(sw|service-?worker)[^/]*\.(js|ts|mjs)$/i.test(f));
    expect(offenders).toEqual([]);
  });

  it('源码里没有注册 Service Worker', () => {
    // 排除本文件自身：它的断言字符串里就写着这些名字
    const self = __filename;
    const offenders = files
      .filter((f) => /\.(ts|tsx|js|jsx)$/.test(f) && f !== self)
      .filter((f) => /navigator\.serviceWorker|serviceWorker\.register/.test(readFileSync(f, 'utf8')));
    expect(offenders).toEqual([]);
  });

  it('Umi 配置没有启用 pwa / workbox 插件', () => {
    const umirc = readFileSync(join(FRONTEND, '.umirc.ts'), 'utf8');
    // 只看配置项的写法，不看注释里的说明文字
    const withoutComments = umirc.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/.*$/gm, '');
    expect(withoutComments).not.toMatch(/\bpwa\b|workbox/i);
  });
});
