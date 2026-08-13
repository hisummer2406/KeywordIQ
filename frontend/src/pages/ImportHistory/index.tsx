import { ReloadOutlined } from '@ant-design/icons';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { App, Button, Tooltip, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { ImportStatusCell, STATUS_META } from '@/components/ImportStatus';
import {
  fetchImportBatches,
  getSites,
  retryImportBatch,
  type ImportBatchRow,
  type SiteOption,
} from '@/services/upload';

const { Text } = Typography;

const PERIOD_ENUM = { DAY: { text: '日报' }, WEEK: { text: '周报' } };

const STATUS_ENUM = Object.fromEntries(
  Object.entries(STATUS_META).map(([code, meta]) => [code, { text: meta.label }]),
);

/** 秒 → 人读的耗时。导入动辄几分钟，纯秒数要在脑子里做除法。 */
function formatDuration(seconds: number) {
  if (!seconds) return '—';
  if (seconds < 60) return `${seconds} 秒`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s ? `${m} 分 ${s} 秒` : `${m} 分`;
}

/**
 * 导入历史屏。
 *
 * 与上传屏的「最近导入状态」看板是两件事，职责刻意分开：
 *
 * - 看板回答「我刚传的文件跑到哪了」——最近 5 条、5 秒轮询、不可筛。
 * - 本屏回答「上周 DE 的周报到底导没导」——分页、三项筛选、**不轮询**。
 *
 * 本屏不轮询是有代价考量的：一张带筛选条件的表每 5 秒整体刷新，用户翻到第 3 页会被
 * 刷回第 1 页。要盯进度就回看板，重导新建的记录是最新一条，本来就在那里的前 5 条里。
 */
export default function ImportHistoryPage() {
  const { message } = App.useApp();
  const actionRef = useRef<ActionType>();
  const [sites, setSites] = useState<SiteOption[]>([]);
  const [retrying, setRetrying] = useState<number | null>(null);

  useEffect(() => {
    getSites()
      .then(setSites)
      .catch(() => undefined);
  }, []);

  const siteEnum = Object.fromEntries(
    sites.map((s) => [s.code, { text: `${s.code} ${s.name_zh}` }]),
  );

  const handleRetry = async (row: ImportBatchRow) => {
    setRetrying(row.id);
    try {
      await retryImportBatch(row.id);
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责，这里只管成功路径
      message.success('已触发重导，稍后可在列表中看到新的批次');
      actionRef.current?.reload();
    } finally {
      setRetrying(null);
    }
  };

  /**
   * 重导入口只在真能重导的行上出现。
   *
   * 非 FAILED 不显示按钮（后端只允许 FAILED）；FAILED 但原始文件未留存的置灰并说明
   * 原因——展示一个点下去必然报 422 的按钮，等于让用户替我们发现这条规则。
   */
  const renderRetry = (row: ImportBatchRow) => {
    if (row.status !== 'FAILED') return <Text type="secondary">—</Text>;
    if (!row.source_retained) {
      return (
        <Tooltip title="该批次的原始文件未留存（本地导入），请重新上传该文件">
          <Button type="link" size="small" icon={<ReloadOutlined />} disabled>
            重导
          </Button>
        </Tooltip>
      );
    }
    return (
      <Button
        type="link"
        size="small"
        // loading 时 antd 会把 icon 换成转圈，正好表达「已经在触发了」
        icon={<ReloadOutlined />}
        loading={retrying === row.id}
        onClick={() => handleRetry(row)}
      >
        重导
      </Button>
    );
  };

  const columns: ProColumns<ImportBatchRow>[] = [
    { title: '文件', dataIndex: 'file_name', ellipsis: true, hideInSearch: true },
    { title: '站点', dataIndex: 'site', width: 120, valueType: 'select', valueEnum: siteEnum },
    {
      title: '周期',
      dataIndex: 'period_type',
      width: 100,
      valueType: 'select',
      valueEnum: PERIOD_ENUM,
    },
    {
      title: '报告日期',
      dataIndex: 'report_date',
      width: 120,
      hideInSearch: true,
    },
    {
      title: '状态',
      dataIndex: 'status',
      width: 200,
      valueType: 'select',
      valueEnum: STATUS_ENUM,
      render: (_, row) => (
        <ImportStatusCell status={row.status} errorMessage={row.error_message} ellipsisRows={2} />
      ),
    },
    {
      // 写入 / 源文件两个数并排。只给百分比会把差值糊掉——99.8% 是少了 600 行还是
      // 6 行，历史场景恰恰关心这个。
      title: '写入 / 源文件行数',
      dataIndex: 'written_rows',
      width: 180,
      hideInSearch: true,
      render: (_, row) => (
        <Text type={row.written_rows < row.total_rows ? 'warning' : undefined}>
          {row.written_rows.toLocaleString()} / {row.total_rows.toLocaleString()}
        </Text>
      ),
    },
    {
      title: '耗时',
      dataIndex: 'processing_seconds',
      width: 100,
      hideInSearch: true,
      render: (_, row) => formatDuration(row.processing_seconds),
    },
    {
      title: '导入时间',
      dataIndex: 'created_at',
      width: 180,
      valueType: 'dateTime',
      hideInSearch: true,
    },
    {
      title: '操作',
      key: 'action',
      width: 90,
      fixed: 'right',
      hideInSearch: true,
      render: (_, row) => renderRetry(row),
    },
  ];

  return (
    <PageContainer title="导入历史">
      <ProTable<ImportBatchRow>
        rowKey="id"
        actionRef={actionRef}
        columns={columns}
        request={fetchImportBatches}
        scroll={{ x: 1200 }}
        pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [20, 50, 100] }}
        options={{ reload: true, density: false, setting: false }}
        dateFormatter="string"
      />
    </PageContainer>
  );
}
