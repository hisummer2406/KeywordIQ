import { defineConfig } from 'vitest/config';
import { resolve } from 'path';

/**
 * 前端测试运行器配置。
 *
 * 不复用 .umirc.ts——Umi 有自己的构建管线，测试只需要把 `@/` 别名与 JSX 处理对齐即可。
 * 组件依赖的 `@umijs/max` 运行时（useAccess/history/request）在各测试里按需 mock，
 * 不在这里做全局替身，避免测试之间互相污染。
 *
 * 不装 @vitejs/plugin-react：它只提供 Fast Refresh，测试用不上，而 v6 是 ESM-only，
 * 会和 CJS 的配置加载打架。JSX 交给 esbuild 的 automatic runtime 即可。
 */
export default defineConfig({
  esbuild: { jsx: 'automatic' },
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    // Umi 生成物与构建产物不参与测试
    exclude: ['node_modules/**', 'dist/**', 'src/.umi/**', 'src/.umi-production/**'],
  },
});
