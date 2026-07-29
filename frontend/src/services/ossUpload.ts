import OSS from 'ali-oss';
import { getUploadCredentials, triggerImport, type Period } from './upload';

/** ali-oss 期望 region 形如 'oss-cn-hangzhou'；后端若给 'cn-hangzhou' 则补前缀。 */
function normalizeRegion(region: string): string {
  if (!region) return region;
  return region.startsWith('oss-') ? region : `oss-${region}`;
}

export interface OssUploadOptions {
  site: string;
  period?: Period;
  onProgress?: (percent: number) => void;
}

/**
 * STS 凭证直传 OSS（分片）→ 回传 object key 触发从 OSS 拉取导入。
 * GB 级字节直传 OSS，不经应用服务器（design 决策 5）。
 */
export async function ossDirectUploadAndImport(
  file: File,
  { site, period, onProgress }: OssUploadOptions,
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
  });
  await client.multipartUpload(cred.object_key, file, {
    progress: (p: number) => onProgress?.(Math.round(p * 100)),
  });

  // 3. 回传 object key，触发后台从 OSS 流式导入
  return triggerImport({ site, filename: file.name, object_key: cred.object_key, period });
}
