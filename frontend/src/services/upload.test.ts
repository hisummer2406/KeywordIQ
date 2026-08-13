import { beforeEach, describe, expect, it, vi } from 'vitest';

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock('@umijs/max', () => ({ request: requestMock }));

import { fetchImportBatches, retryImportBatch } from '@/services/upload';

/**
 * 导入历史接口的参数映射。
 *
 * ProTable 的分页字段是 `current`/`pageSize`，后端是 `page`/`per_page`；表格列名用
 * `period_type`（与行字段同名，渲染与筛选才是同一个 key），后端参数是 `period`。
 * 这层换算是纯函数，值得钉住——错了的表现是「筛选选了没反应」，而不是报错。
 */
describe('导入历史接口', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    requestMock.mockResolvedValue({ data: [], total: 0, success: true });
  });

  it('分页字段由 ProTable 名映射为后端名', async () => {
    await fetchImportBatches({ current: 3, pageSize: 50 });

    const [, options] = requestMock.mock.calls[0];
    expect(options.params).toMatchObject({ page: 3, per_page: 50 });
    expect(options.params).not.toHaveProperty('current');
    expect(options.params).not.toHaveProperty('pageSize');
  });

  it('缺省分页有兜底值，不把 undefined 发出去', async () => {
    await fetchImportBatches({});

    const [, options] = requestMock.mock.calls[0];
    expect(options.params).toMatchObject({ page: 1, per_page: 20 });
  });

  it('period_type 映射为后端的 period，站点与状态原样传', async () => {
    await fetchImportBatches({ site: 'DE', period_type: 'WEEK', status: 'FAILED' });

    const [, options] = requestMock.mock.calls[0];
    expect(options.params).toMatchObject({ site: 'DE', period: 'WEEK', status: 'FAILED' });
  });

  it('列表响应缺字段时给出安全默认，不让表格拿到 undefined', async () => {
    requestMock.mockResolvedValue({});
    await expect(fetchImportBatches({})).resolves.toEqual({ data: [], total: 0, success: true });
  });

  it('重导按 id 打到对应路径，用 POST', async () => {
    requestMock.mockResolvedValue({ source_batch_id: 7 });
    await retryImportBatch(7);

    const [url, options] = requestMock.mock.calls[0];
    expect(url).toBe('/api/upload/batches/7/retry');
    expect(options.method).toBe('POST');
  });

  it('重导失败向上抛，交统一出口提示', async () => {
    requestMock.mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        response: {
          status: 422,
          data: { code: 'upload.source_not_retained', message: '该批次的原始文件未留存' },
        },
      }),
    );

    await expect(retryImportBatch(7)).rejects.toMatchObject({ response: { status: 422 } });
  });
});
