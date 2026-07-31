import { beforeEach, describe, expect, it, vi } from 'vitest';

const { ossCtor, multipartUpload, requestMock } = vi.hoisted(() => ({
  ossCtor: vi.fn(),
  // 显式给出三参签名（用类型而非带参实现，避免 no-unused-vars），
  // 否则 mockImplementationOnce 里带参数的实现过不了 tsc
  multipartUpload: vi.fn<
    (key: string, file: File, opts?: { progress?: (p: number) => void }) => Promise<{
      res: { status: number };
    }>
  >(),
  requestMock: vi.fn(),
}));

// ali-oss 打桩：单测不连真实 OSS，只验证"传给 SDK 的参数"与三步顺序
vi.mock('ali-oss', () => ({
  default: class {
    constructor(opts: unknown) {
      ossCtor(opts);
    }
    multipartUpload = multipartUpload;
  },
}));
vi.mock('@umijs/max', () => ({ request: requestMock }));

import { ossDirectUploadAndImport } from '@/services/ossUpload';

const CRED = {
  object_key: 'imports/US/DAY/2026-07-18/ab12cd34_US_Day.csv',
  bucket: 'keywordiq-imports',
  endpoint: 'https://oss-cn-hangzhou.aliyuncs.com',
  region: 'cn-hangzhou',
  credentials: {
    access_key_id: 'STS.ak',
    access_key_secret: 'STS.sk',
    security_token: 'tok',
    expiration: '2026-07-18T01:00:00Z',
  },
  site: 'US',
  period: 'DAY',
  report_date: '2026-07-18',
  filename: 'US_Day.csv',
};

/**
 * 直传三步：换 STS 凭证 → ali-oss 分片直传 → 回传 object key 触发导入。
 * 重点守住 object key 全程由后端给定（前端不自造，否则会越权到别的站点/期次），
 * 以及 region 前缀这个易错的 SDK 约定。
 */
describe('ossDirectUploadAndImport', () => {
  const file = new File(['a,b\n1,2\n'], 'US_Day.csv', { type: 'text/csv' });

  beforeEach(() => {
    vi.clearAllMocks();
    requestMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/upload-credentials')) return { status: 0, data: CRED };
      if (url.endsWith('/oss/import')) return { status: 0, data: { batch: 1 } };
      throw new Error(`未预期的请求 ${url}`);
    });
  });

  it('三步按序走完，object key 全程用后端给的那个', async () => {
    await ossDirectUploadAndImport(file, { site: 'US', period: 'DAY' });

    const urls = requestMock.mock.calls.map((c) => c[0]);
    expect(urls[0]).toContain('/upload-credentials');
    expect(urls[1]).toContain('/oss/import');

    // 直传与回传用的都是后端签发的 key，前端不自造
    expect(multipartUpload).toHaveBeenCalledWith(CRED.object_key, file, expect.anything());
    expect(requestMock.mock.calls[1][1].data.object_key).toBe(CRED.object_key);
  });

  it('region 补 oss- 前缀（ali-oss 要 oss-cn-hangzhou，后端给 cn-hangzhou）', async () => {
    await ossDirectUploadAndImport(file, { site: 'US' });
    expect(ossCtor).toHaveBeenCalledWith(expect.objectContaining({ region: 'oss-cn-hangzhou' }));
  });

  it('region 已带前缀时不重复添加', async () => {
    requestMock.mockImplementation(async (url: string) =>
      url.endsWith('/upload-credentials')
        ? { status: 0, data: { ...CRED, region: 'oss-cn-hangzhou' } }
        : { status: 0, data: {} },
    );
    await ossDirectUploadAndImport(file, { site: 'US' });
    expect(ossCtor).toHaveBeenCalledWith(expect.objectContaining({ region: 'oss-cn-hangzhou' }));
  });

  it('用 STS 临时凭证构造客户端（含 stsToken），不使用长期 AK', async () => {
    await ossDirectUploadAndImport(file, { site: 'US' });
    expect(ossCtor).toHaveBeenCalledWith(
      expect.objectContaining({
        accessKeyId: 'STS.ak',
        stsToken: 'tok',
        bucket: 'keywordiq-imports',
        secure: true,
      }),
    );
  });

  it('进度回调把 0..1 换算成百分比', async () => {
    const onProgress = vi.fn();
    multipartUpload.mockImplementationOnce(async (_key, _file, opts) => {
      opts?.progress?.(0.42);
      return { res: { status: 200 } };
    });

    await ossDirectUploadAndImport(file, { site: 'US', onProgress });
    expect(onProgress).toHaveBeenCalledWith(42);
  });

  it('凭证签发失败就不该上传（amis 信封 status≠0 视为失败）', async () => {
    requestMock.mockImplementation(async () => ({ status: 1, msg: '站点未启用' }));

    await expect(ossDirectUploadAndImport(file, { site: 'XX' })).rejects.toThrow('站点未启用');
    expect(multipartUpload).not.toHaveBeenCalled();
  });

  it('直传失败就不该触发导入（避免后端去拉一个不存在的对象）', async () => {
    multipartUpload.mockRejectedValueOnce(new Error('network down'));

    await expect(ossDirectUploadAndImport(file, { site: 'US' })).rejects.toThrow('network down');
    const urls = requestMock.mock.calls.map((c) => c[0]);
    expect(urls.some((u: string) => u.endsWith('/oss/import'))).toBe(false);
  });
});
