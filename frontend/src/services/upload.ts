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
  /**
   * 续签既有上传的凭证时传本次的 object key。传了后端就认出这是续签——只签发凭证、
   * 不新建批次记录、不生成新 key。不传即为一次新上传。
   */
  object_key?: string;
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

/**
 * 导入历史的一行。
 *
 * 没有 `object_key`——它含站点/期次/日期/原始文件名，属内部细节，后端不序列化。
 * 界面需要的是「这一行还能不能重导」，那就是 `source_retained`。
 */
export interface ImportBatchRow {
  id: number;
  site: string;
  period_type: Period;
  report_date: string;
  file_name: string;
  status: ImportStatus | string;
  total_rows: number;
  written_rows: number;
  processing_seconds: number;
  error_message: string;
  created_at: string;
  completed_at: string | null;
  /** 原始文件是否仍可据以重导（本地退路导入的批次为 false）。 */
  source_retained: boolean;
}

/** ProTable request：拉分页的导入历史（`{data,total,success}`）。 */
export async function fetchImportBatches(params: {
  current?: number;
  pageSize?: number;
  site?: string;
  period_type?: Period;
  status?: string;
}) {
  const res = await request<{ data: ImportBatchRow[]; total: number; success: boolean }>(
    '/api/upload/batches',
    {
      method: 'GET',
      params: {
        page: params.current ?? 1,
        per_page: params.pageSize ?? 20,
        site: params.site,
        // 后端参数名是 period；表格列名沿用 period_type 与行字段一致，此处做映射
        period: params.period_type,
        status: params.status,
      },
    },
  );
  return { data: res.data ?? [], total: res.total ?? 0, success: res.success !== false };
}

/**
 * 重导一个失败批次。凭后端留存的 object key 重新导入，不必重传原始文件。
 *
 * 响应里没有新批次 id：批次记录由导入内核在后台创建，此刻还不存在。调用方刷新列表即可。
 */
export async function retryImportBatch(id: number) {
  return request<{
    source_batch_id: number;
    site: string;
    period: Period;
    report_date: string;
    file_name: string;
  }>(`/api/upload/batches/${id}/retry`, { method: 'POST' });
}
