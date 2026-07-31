import { request } from '@umijs/max';

/** OSS 直传相关接口。后端已去 amis 信封，直接返数据；失败走统一错误契约的非 2xx，
 * 由 app.tsx 的 errorHandler 唯一出口提示，这里不再解包判状态。 */

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

export interface ImportTriggered {
  object_key: string;
  site: string;
  period: string;
  report_date: string;
}

export interface ImportStatusItem {
  batch_name: string;
  progress_percent: number;
  total_records: number;
  status: string;
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
  return request<OssCredential>('/api/upload/oss/upload-credentials', {
    method: 'POST',
    data: body,
  });
}

export async function triggerImport(body: {
  site: string;
  filename: string;
  object_key: string;
  period?: Period;
}) {
  return request<ImportTriggered>('/api/upload/oss/import', {
    method: 'POST',
    data: body,
  });
}

export async function getProcessingStatus(dataType?: 'daily' | 'weekly') {
  return request<{ items: ImportStatusItem[] }>('/api/upload/processing-status', {
    method: 'GET',
    params: dataType ? { data_type: dataType } : {},
  });
}
