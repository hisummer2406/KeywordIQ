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

/** 批次状态码。中文文案由界面负责，接口只回状态码。 */
export type ImportStatus = 'QUEUED' | 'PROCESSING' | 'COMPLETED' | 'FAILED';

export interface ImportStatusItem {
  batch_name: string;
  progress_percent: number;
  total_records: number;
  status: ImportStatus | string;
  /** 失败原因，或成功时的备注（跳过行数等）。无则为空串。 */
  error_message?: string;
}

/** 文件名解析出的归属三元组。判定不出的项为 null，并列在 needs 里。 */
export interface FilenameMeta {
  filename: string;
  site: string | null;
  period: Period | null;
  report_date: string | null;
  needs: ('site' | 'period' | 'report_date')[];
}

export async function getSites() {
  const resp = await request<{ sites: SiteOption[] }>('/api/upload/sites', { method: 'GET' });
  return resp.sites;
}

/**
 * 上传前解析文件名，取得站点/周期/报告日期。
 *
 * 走后端而不在前端写一份正则：解析规则（站点前缀、日期格式、周期关键词）的唯一实现在
 * `app/table/upload/importer.py`，两处各写一份迟早各自演化，届时界面回显的归属与实际
 * 写入的归属会不一致——而这个三元组决定数据落到哪张事实表的哪个分区。
 */
export async function inspectFilename(filename: string) {
  return request<FilenameMeta>('/api/upload/inspect-filename', {
    method: 'POST',
    data: { filename },
  });
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
