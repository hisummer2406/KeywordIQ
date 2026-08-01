import OSS from 'ali-oss';
import { getUploadCredentials, triggerImport, type Period } from './upload';

/** ali-oss 期望 region 形如 'oss-cn-hangzhou'；后端若给 'cn-hangzhou' 则补前缀。 */
function normalizeRegion(region: string): string {
  if (!region) return region;
  return region.startsWith('oss-') ? region : `oss-${region}`;
}

/**
 * 凭证续期间隔。刻意取一个远小于凭证有效期的**固定值**，而不是按 expiration 推算：
 * 有效期由后端 `OSS_STS_DURATION_SECONDS` 决定（默认 3600 秒，可被调小），按 expiration
 * 推算还要处理两端时钟偏差。5 分钟一次 AssumeRole 的代价可以忽略，换来的是配置怎么改都
 * 不会追不上过期。
 */
const STS_REFRESH_INTERVAL_MS = 5 * 60 * 1000;

/** 直传尝试次数上限（含首次）。断点续传下重试只补未完成的分片，代价很低。 */
const MAX_ATTEMPTS = 3;

const sleep = (ms: number) =>
  new Promise<void>((resolve) => {
    setTimeout(resolve, ms);
  });

export interface OssUploadOptions {
  site: string;
  period?: Period;
  onProgress?: (percent: number) => void;
  /** 重试前的等待时长，默认 1 秒；测试传 0 以免空等。 */
  retryDelayMs?: number;
}

/** ali-oss 的断点续传句柄，内含 uploadId 与已完成分片。结构由 SDK 定义，此处只透传。 */
type Checkpoint = { uploadId?: string } & Record<string, unknown>;

/**
 * STS 凭证直传 OSS（分片）→ 回传 object key 触发从 OSS 拉取导入。
 * GB 级字节直传 OSS，不经应用服务器（design 决策 5）。
 *
 * 目标体量是 1.5GB 的周报，三件事因此是必需的而非锦上添花：
 *
 * - **凭证续期**：1.5GB / 3600s ≈ 427 KB/s。上行低于这个数，凭证就会在传完之前过期，
 *   剩余分片全部 403。静态 stsToken 无解，必须给 SDK `refreshSTSToken` 回调。
 * - **断点续传**：一次网络抖动不该让已传的 GB 级字节作废，重试带上 checkpoint 只补
 *   未完成的分片。
 * - **放弃时收走分片**：未完成的分片会留在 bucket 里持续计费且无人知晓。这里只覆盖
 *   "本次会话内放弃"的情形；页面刷新/关闭后残留的分片，得靠 bucket 生命周期规则兜底。
 *
 * checkpoint 只在内存里维持，不跨页面刷新——它引用着 File 对象，序列化不了；刷新后浏览器
 * 也拿不回原文件句柄，用户必须重新选文件，届时会走一次全新的上传。
 */
export async function ossDirectUploadAndImport(
  file: File,
  { site, period, onProgress, retryDelayMs = 1000 }: OssUploadOptions,
) {
  // 1. 换受限 STS 凭证 + object key（后端 RAM 策略限死到本次 bucket/前缀/仅写/短有效期）
  const cred = await getUploadCredentials({ site, filename: file.name, period });

  // 2. ali-oss 分片直传
  const client = new OSS({
    region: normalizeRegion(cred.region),
    accessKeyId: cred.credentials.access_key_id,
    accessKeySecret: cred.credentials.access_key_secret,
    stsToken: cred.credentials.security_token,
    bucket: cred.bucket,
    secure: true,
    refreshSTSTokenInterval: STS_REFRESH_INTERVAL_MS,
    // 续期只取凭证部分。后端每次签发都会生成一个新的 object key，这里**必须丢弃**它——
    // 本次上传自始至终写同一个 key，换 key 会把一个文件传成两个对象。新凭证的 RAM 策略
    // 限定的是前缀而非具体 key，因此对已在传的 key 依然有效。
    refreshSTSToken: async () => {
      const fresh = await getUploadCredentials({ site, filename: file.name, period });
      return {
        accessKeyId: fresh.credentials.access_key_id,
        accessKeySecret: fresh.credentials.access_key_secret,
        stsToken: fresh.credentials.security_token,
      };
    },
  });

  let checkpoint: Checkpoint | undefined;
  let lastError: unknown;

  // 定义在循环外：回调闭包引用的是可变的 checkpoint，放进循环里既触发 lint 的
  // no-loop-func，也确实容易读错——每轮拿到的必须是**上一轮留下的最新**进度。
  const attemptUpload = () =>
    client.multipartUpload(cred.object_key, file, {
      checkpoint,
      progress: (p: number, cpt?: Checkpoint) => {
        // SDK 每完成一片回调一次并给出最新 checkpoint，留作下次重试的起点
        if (cpt) checkpoint = cpt;
        onProgress?.(Math.round(p * 100));
      },
    } as Parameters<typeof client.multipartUpload>[2]);

  for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt += 1) {
    try {
      await attemptUpload();
      lastError = undefined;
      break;
    } catch (e) {
      lastError = e;
      if (attempt < MAX_ATTEMPTS && retryDelayMs > 0) await sleep(retryDelayMs);
    }
  }

  if (lastError) {
    // 收走未完成的分片。清理失败不改变结论——抛出去的必须是原始错误，否则真正的失败原因
    // 会被一个次要错误盖住。
    if (checkpoint?.uploadId) {
      try {
        await client.abortMultipartUpload(cred.object_key, checkpoint.uploadId);
      } catch {
        /* 清理失败只影响残留分片，交由 bucket 生命周期规则兜底 */
      }
    }
    throw lastError;
  }

  // 3. 回传 object key，触发后台从 OSS 流式导入
  return triggerImport({ site, filename: file.name, object_key: cred.object_key, period });
}
