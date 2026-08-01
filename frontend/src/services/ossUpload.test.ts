import { beforeEach, describe, expect, it, vi } from 'vitest';

const { ossCtor, multipartUpload, abortMultipartUpload, requestMock } = vi.hoisted(() => ({
  ossCtor: vi.fn(),
  // 显式给出三参签名（用类型而非带参实现，避免 no-unused-vars），
  // 否则 mockImplementationOnce 里带参数的实现过不了 tsc
  multipartUpload: vi.fn<
    (
      key: string,
      file: File,
      opts?: {
        progress?: (p: number, checkpoint?: unknown) => void;
        checkpoint?: unknown;
      },
    ) => Promise<{ res: { status: number } }>
  >(),
  abortMultipartUpload: vi.fn(),
  requestMock: vi.fn(),
}));

// ali-oss 打桩：单测不连真实 OSS，只验证"传给 SDK 的参数"与三步顺序
vi.mock('ali-oss', () => ({
  default: class {
    constructor(opts: unknown) {
      ossCtor(opts);
    }
    multipartUpload = multipartUpload;
    abortMultipartUpload = abortMultipartUpload;
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
    // clearAllMocks 只清调用记录、不清实现，用 mockReset 免得上一条用例的 reject 泄漏过来
    multipartUpload.mockReset();
    multipartUpload.mockResolvedValue({ res: { status: 200 } });
    abortMultipartUpload.mockReset();
    // 后端已去 amis 信封：成功直接返数据，失败是非 2xx（request 会 reject）
    requestMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/upload-credentials')) return CRED;
      if (url.endsWith('/oss/import')) return { object_key: CRED.object_key };
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
      url.endsWith('/upload-credentials') ? { ...CRED, region: 'oss-cn-hangzhou' } : {},
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

  it('凭证签发失败就不该上传（失败是非 2xx，request 直接 reject）', async () => {
    requestMock.mockRejectedValue(new Error('站点未启用'));

    await expect(ossDirectUploadAndImport(file, { site: 'XX' })).rejects.toThrow('站点未启用');
    expect(multipartUpload).not.toHaveBeenCalled();
  });

  it('直传失败就不该触发导入（避免后端去拉一个不存在的对象）', async () => {
    multipartUpload.mockRejectedValue(new Error('network down'));

    await expect(
      ossDirectUploadAndImport(file, { site: 'US', retryDelayMs: 0 }),
    ).rejects.toThrow('network down');
    const urls = requestMock.mock.calls.map((c) => c[0]);
    expect(urls.some((u: string) => u.endsWith('/oss/import'))).toBe(false);
  });
});

/**
 * GB 级文件才会暴露的三件事，小文件测不出来：
 *
 * 1. **凭证会在传输途中过期**。1.5GB / 3600s ≈ 427 KB/s——上行低于这个数，凭证就在传完
 *    之前失效，剩余分片全部 403。静态 stsToken 无解，必须给 SDK 续期回调。
 * 2. **一次网络抖动不该让 1.5GB 从头再来**。分片上传的 checkpoint 就是为此存在的。
 * 3. **放弃时要把未完成的分片收走**，否则它们留在 bucket 里持续计费且无人知晓。
 */
describe('大文件直传的韧性', () => {
  const file = new File(['a,b\n1,2\n'], 'US_Day.csv', { type: 'text/csv' });

  beforeEach(() => {
    vi.clearAllMocks();
    // clearAllMocks 只清调用记录、不清实现，用 mockReset 免得上一条用例的 reject 泄漏过来
    multipartUpload.mockReset();
    multipartUpload.mockResolvedValue({ res: { status: 200 } });
    abortMultipartUpload.mockReset();
    requestMock.mockImplementation(async (url: string) => {
      if (url.endsWith('/upload-credentials')) return CRED;
      if (url.endsWith('/oss/import')) return { object_key: CRED.object_key };
      throw new Error(`未预期的请求 ${url}`);
    });
  });

  it('给 SDK 装上凭证续期回调，且续期间隔短于凭证有效期', async () => {
    await ossDirectUploadAndImport(file, { site: 'US' });

    const opts = ossCtor.mock.calls[0][0];
    expect(typeof opts.refreshSTSToken).toBe('function');
    expect(opts.refreshSTSTokenInterval).toBeGreaterThan(0);
    // 后端默认 OSS_STS_DURATION_SECONDS=3600；间隔必须远小于它，否则续期赶不上过期
    expect(opts.refreshSTSTokenInterval).toBeLessThanOrEqual(10 * 60 * 1000);
  });

  it('续期回调重新向后端换凭证，返回 SDK 要的三个字段', async () => {
    await ossDirectUploadAndImport(file, { site: 'US', period: 'DAY' });
    const { refreshSTSToken } = ossCtor.mock.calls[0][0];

    const before = requestMock.mock.calls.length;
    const fresh = await refreshSTSToken();

    expect(requestMock.mock.calls[before][0]).toContain('/upload-credentials');
    expect(fresh).toEqual({
      accessKeyId: CRED.credentials.access_key_id,
      accessKeySecret: CRED.credentials.access_key_secret,
      stsToken: CRED.credentials.security_token,
    });
  });

  it('中断后按 checkpoint 续传，object key 不变（不会传成两个对象）', async () => {
    const cpt = { uploadId: 'up-1', doneParts: [{ number: 1, etag: 'e1' }] };
    multipartUpload
      .mockImplementationOnce(async (_key, _file, opts) => {
        opts?.progress?.(0.5, cpt); // 传到一半拿到 checkpoint
        throw new Error('network blip');
      })
      .mockResolvedValueOnce({ res: { status: 200 } });

    await ossDirectUploadAndImport(file, { site: 'US', retryDelayMs: 0 });

    expect(multipartUpload).toHaveBeenCalledTimes(2);
    const [key2, , opts2] = multipartUpload.mock.calls[1];
    expect(key2).toBe(CRED.object_key);
    expect(opts2?.checkpoint).toBe(cpt);
    // 续传成功后照常触发导入
    expect(requestMock.mock.calls.some((c) => String(c[0]).endsWith('/oss/import'))).toBe(true);
  });

  it('首片就失败（无 checkpoint）也会重试，只是从头传', async () => {
    multipartUpload
      .mockRejectedValueOnce(new Error('connection reset'))
      .mockResolvedValueOnce({ res: { status: 200 } });

    await ossDirectUploadAndImport(file, { site: 'US', retryDelayMs: 0 });

    expect(multipartUpload).toHaveBeenCalledTimes(2);
    expect(multipartUpload.mock.calls[1][2]?.checkpoint).toBeUndefined();
  });

  it('重试用尽后抛错、不触发导入，并收走未完成的分片', async () => {
    const cpt = { uploadId: 'up-9', doneParts: [] };
    multipartUpload.mockImplementation(async (_key, _file, opts) => {
      opts?.progress?.(0.1, cpt);
      throw new Error('still down');
    });

    await expect(
      ossDirectUploadAndImport(file, { site: 'US', retryDelayMs: 0 }),
    ).rejects.toThrow('still down');

    expect(multipartUpload).toHaveBeenCalledTimes(3);
    expect(abortMultipartUpload).toHaveBeenCalledWith(CRED.object_key, 'up-9');
    expect(requestMock.mock.calls.some((c) => String(c[0]).endsWith('/oss/import'))).toBe(false);
  });

  it('收尾清理失败不掩盖原始错误', async () => {
    const cpt = { uploadId: 'up-9', doneParts: [] };
    multipartUpload.mockImplementation(async (_key, _file, opts) => {
      opts?.progress?.(0.1, cpt);
      throw new Error('原始错误');
    });
    abortMultipartUpload.mockRejectedValue(new Error('abort 也失败了'));

    await expect(
      ossDirectUploadAndImport(file, { site: 'US', retryDelayMs: 0 }),
    ).rejects.toThrow('原始错误');
  });
});
