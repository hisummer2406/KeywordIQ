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
    // **默认的 5 秒超时对这套测试太紧。** 分析屏那类用例要渲染整屏（作用域栏 + 过滤区 +
    // 50 列宽的 ProTable），单条实测 4 秒上下；并行跑满 CPU 时就会零星翻红，而翻红的
    // 用例每次都不同、报的又是超时而非断言失败——那种"随机红"比慢更贵，它会让人开始
    // 忽略红灯。放宽到 20 秒不掩盖任何断言：真挂住的用例照样会红，只是晚 15 秒。
    testTimeout: 20000,
    setupFiles: ['./src/test/setup.ts'],
    // Umi 生成物与构建产物不参与测试
    exclude: ['node_modules/**', 'dist/**', 'src/.umi/**', 'src/.umi-production/**'],
  },
});
