import { beforeEach, describe, expect, it, vi } from 'vitest';

// vi.mock 会被提升到文件顶部，工厂里不能引用普通的顶层变量，故用 vi.hoisted 一起提升
const { pushMock } = vi.hoisted(() => ({ pushMock: vi.fn() }));
vi.mock('@umijs/max', () => ({
  history: { push: pushMock },
  request: vi.fn(),
}));

import { exportAnalysis } from '@/services/analysis';
import { getToken, setToken, TOKEN_KEY } from '@/utils/token';

/**
 * 导出用裸 fetch（需要 Blob 与响应头里的文件名），因此绕过了 app.tsx 的 errorConfig。
 * 这几条守住的正是"绕过之后自己补上的那部分"：文件名、413 原文、401 清 token 跳登录。
 */
describe('exportAnalysis', () => {
  const createObjectURL = vi.fn(() => 'blob:fake');
  const revokeObjectURL = vi.fn();
  // 用工厂推导 spy 类型，直接写 ReturnType<typeof vi.spyOn> 会丢掉 appendChild 的泛型签名
  const spyAppend = () => vi.spyOn(document.body, 'appendChild');
  let appendSpy: ReturnType<typeof spyAppend>;

  /** 取被塞进 DOM 的那个下载锚点（下载靠它的 download 属性 + click 触发）。 */
  const downloadAnchor = () => appendSpy.mock.calls[0][0] as HTMLAnchorElement;

  beforeEach(() => {
    vi.clearAllMocks();
    // jsdom 没有实现 createObjectURL；点击锚点会触发未实现的导航，一并空转掉
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined);
    appendSpy = spyAppend();
  });

  const mockFetch = (init: { status: number; body?: unknown; disposition?: string }) => {
    const res = {
      ok: init.status >= 200 && init.status < 300,
      status: init.status,
      headers: { get: () => init.disposition ?? null },
      blob: async () => new Blob(['csv']),
      json: async () => init.body,
    };
    vi.stubGlobal('fetch', vi.fn(async () => res));
    return res;
  };

  it('成功时按 Content-Disposition 的文件名触发下载', async () => {
    mockFetch({ status: 200, disposition: 'attachment; filename="US_DAY_2026-07-18.csv"' });

    await exportAnalysis({ site: 'US', view: 'DAY' });

    expect(createObjectURL).toHaveBeenCalledOnce();
    expect(downloadAnchor().download).toBe('US_DAY_2026-07-18.csv');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:fake'); // 不泄漏 object URL
  });

  it('响应头缺文件名时用兜底名，不至于下载出无名文件', async () => {
    mockFetch({ status: 200 });
    await exportAnalysis({ site: 'US', view: 'DAY' });
    expect(downloadAnchor().download).toBe('export.csv');
  });

  it('带上 Bearer token', async () => {
    setToken('tok-123');
    mockFetch({ status: 200 });

    await exportAnalysis({ site: 'US', view: 'DAY' });

    const [, init] = (fetch as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(init.headers.Authorization).toBe('Bearer tok-123');
  });

  it('413 把后端原文透出来（提示加筛选，而不是给一份截断数据）', async () => {
    mockFetch({ status: 413, body: { detail: '导出结果超过 50000 行上限，请补充筛选条件后再导出' } });

    await expect(exportAnalysis({ site: 'US', view: 'DAY' })).rejects.toThrow(/50000 行上限/);
    expect(createObjectURL).not.toHaveBeenCalled();
  });

  it('401 清 token 并跳登录（补上绕过 errorConfig 后缺的那段）', async () => {
    setToken('expired');
    mockFetch({ status: 401 });

    await expect(exportAnalysis({ site: 'US', view: 'DAY' })).rejects.toThrow(/登录已过期/);
    expect(getToken()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
    expect(pushMock).toHaveBeenCalledWith('/login');
  });

  it('错误体不是 JSON 时用兜底文案，不把解析异常抛给用户', async () => {
    const res = {
      ok: false,
      status: 502,
      headers: { get: () => null },
      json: async () => {
        throw new SyntaxError('Unexpected token < in JSON');
      },
    };
    vi.stubGlobal('fetch', vi.fn(async () => res));

    await expect(exportAnalysis({ site: 'US', view: 'DAY' })).rejects.toThrow(/导出失败（502）/);
  });
});
