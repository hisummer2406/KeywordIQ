import { beforeEach, describe, expect, it, vi } from 'vitest';

// vi.mock 会被提升到文件顶部，工厂里不能引用普通的顶层变量，故用 vi.hoisted 一起提升
const { pushMock, clearSnapshotMock } = vi.hoisted(() => ({
  pushMock: vi.fn(),
  clearSnapshotMock: vi.fn(),
}));
vi.mock('@umijs/max', () => ({
  history: { push: pushMock },
  request: vi.fn(),
}));
vi.mock('@/pages/Analysis/stateCache', () => ({ clearSnapshot: clearSnapshotMock }));

import { exportAnalysis } from '@/services/analysis';
import { clearToken, getToken, setToken, TOKEN_KEY } from '@/utils/token';

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
    // 统一错误契约（此前是 FastAPI 的 {detail}）
    mockFetch({
      status: 413,
      body: {
        success: false,
        code: 'analysis.export_too_large',
        message: '导出结果超过 50000 行上限，请补充筛选条件后再导出',
        request_id: 'abc123',
      },
    });

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

  it('会话被顶替时一并清空分析屏会话缓存，避免下一个账号看到上一个账号的条件（PR #91 复审 P1）', async () => {
    setToken('kicked');
    mockFetch({ status: 401 });

    await expect(exportAnalysis({ site: 'US', view: 'DAY' })).rejects.toThrow();
    expect(clearSnapshotMock).toHaveBeenCalledOnce();
  });

  it('会话被顶替时呈现区别于普通过期的明确原因（issue #89）', async () => {
    setToken('kicked');
    mockFetch({
      status: 401,
      body: { success: false, code: 'auth.session_invalid', message: '登录状态已失效' },
    });

    await expect(exportAnalysis({ site: 'US', view: 'DAY' })).rejects.toThrow(/账号已在其他浏览器登录/);
    expect(getToken()).toBeNull();
    expect(pushMock).toHaveBeenCalledWith('/login');
  });

  it('导出请求发出后本浏览器已重新登录，姗姗来迟的旧 401 不清理新会话（PR #91 复审 P2）', async () => {
    // 导出发起时用的是旧 token；响应还没回来，本浏览器已经用新 token 完成一次登录
    setToken('stale-before-relogin');
    const res = {
      ok: false,
      status: 401,
      headers: { get: () => null },
      blob: async () => new Blob(['csv']),
      json: async () => ({ success: false, code: 'auth.session_invalid', message: 'x' }),
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        // fetch 发出之后、响应落地之前，本浏览器已经完成了一次新登录
        setToken('fresh-after-relogin');
        return res;
      }),
    );

    await exportAnalysis({ site: 'US', view: 'DAY' }); // 不应该 reject

    expect(getToken()).toBe('fresh-after-relogin');
    expect(pushMock).not.toHaveBeenCalled();
    expect(clearSnapshotMock).not.toHaveBeenCalled();
  });

  it('res.json() 解析期间才完成重新登录，同样不清理新会话（PR #91 复审第四轮）', async () => {
    // 令牌比对在拿到 res 时还是一致的；真正的竞态发生在随后 await res.json() 这段
    // 异步间隙里——必须在真正清理前再判一次，只判一次不够。
    setToken('stale-before-relogin');
    const res = {
      ok: false,
      status: 401,
      headers: { get: () => null },
      blob: async () => new Blob(['csv']),
      json: async () => {
        setToken('fresh-after-relogin');
        return { success: false, code: 'auth.session_invalid', message: 'x' };
      },
    };
    vi.stubGlobal('fetch', vi.fn(async () => res));

    await exportAnalysis({ site: 'US', view: 'DAY' }); // 不应该 reject

    expect(getToken()).toBe('fresh-after-relogin');
    expect(pushMock).not.toHaveBeenCalled();
    expect(clearSnapshotMock).not.toHaveBeenCalled();
  });

  it('导出请求发出后本浏览器已经退出（另一标签页触发，不是重新登录），401 正常清理并提示，不当成陈旧响应静默吞掉（PR #91 复审第七轮）', async () => {
    setToken('used-before-logout');
    const res = {
      ok: false,
      status: 401,
      headers: { get: () => null },
      blob: async () => new Blob(['csv']),
      json: async () => ({ success: false, code: 'auth.session_invalid', message: 'x' }),
    };
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        // fetch 发出之后、响应落地之前，另一个标签页完成了退出——本地令牌变成 null，
        // 不是被替换成另一张新令牌
        clearToken();
        return res;
      }),
    );

    await expect(exportAnalysis({ site: 'US', view: 'DAY' })).rejects.toThrow(/账号已在其他浏览器登录/);

    expect(getToken()).toBeNull();
    expect(pushMock).toHaveBeenCalledWith('/login');
    expect(clearSnapshotMock).toHaveBeenCalledOnce();
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
