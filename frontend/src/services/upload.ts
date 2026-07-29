import { request } from '@umijs/max';

/** OSS 直传相关接口。凭证/导入/状态沿用后端既有 amis 信封 {status,msg,data}，此处解包判 status。 */

export type Period = 'DAY' | 'WEEK';

export interface SiteOption {
  code: string;
  name_zh: string;
  domain: string;
}

export interface OssCredential {
  object_key: string;
  bucket: string;
  endpoint: string;
  region: string;
  credentials: {
    access_key_id: string;
    access_key_secret: string;
    security_token: string;
    expiration: string;
  };
  site: string;
  period: string;
  report_date: string;
  filename: string;
}

export interface ImportStatusItem {
  batch_name: string;
  progress_percent: number;
  total_records: number;
  status: string;
}

interface AmisEnvelope<T> {
  status: number;
  msg?: string;
  data?: T;
}

/** 解包 amis 信封：status===0 取 data，否则抛出 msg。 */
function unwrap<T>(resp: AmisEnvelope<T>): T {
  if (resp.status !== 0) {
    throw new Error(resp.msg || '请求失败');
  }
  return resp.data as T;
}

export async function getSites() {
  const resp = await request<{ sites: SiteOption[] }>('/api/upload/sites', { method: 'GET' });
  return resp.sites;
}

export async function getUploadCredentials(body: {
  site: string;
  filename: string;
  period?: Period;
}) {
  const resp = await request<AmisEnvelope<OssCredential>>('/api/upload/oss/upload-credentials', {
    method: 'POST',
    data: body,
  });
  return unwrap(resp);
}

export async function triggerImport(body: {
  site: string;
  filename: string;
  object_key: string;
  period?: Period;
}) {
  const resp = await request<AmisEnvelope<unknown>>('/api/upload/oss/import', {
    method: 'POST',
    data: body,
  });
  return unwrap(resp);
}

export async function getProcessingStatus(dataType?: 'daily' | 'weekly') {
  const resp = await request<AmisEnvelope<{ items: ImportStatusItem[]; active_sessions?: number }>>(
    '/api/upload/processing-status',
    { method: 'GET', params: dataType ? { data_type: dataType } : {} },
  );
  return unwrap(resp);
}
