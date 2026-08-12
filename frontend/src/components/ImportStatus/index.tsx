import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import { Space, Tag, Typography } from 'antd';
import type { ReactNode } from 'react';

const { Text } = Typography;

/**
 * 批次状态的呈现。文案在前端而不在接口里：接口只回状态码。
 *
 * **两个屏共用这一份。** 上传屏的「最近导入状态」看板与导入历史屏显示的是同一张表的
 * 同一组状态；各写一份的结果是同一个状态在两个屏上叫不同名字，而这种偏差在 review 里
 * 几乎看不出来——两个文件分别看都是对的。
 *
 * `QUEUED` 的说明是这一格的全部意义。它曾经是个死标签（批次停在那里，没有任何东西
 * 会接手），现在由后端的接手任务在前一个导入结束后自动开始——不把这句话说出来，
 * 用户看见「排队中」仍然不知道该等还是该重传。
 */
export const STATUS_META: Record<
  string,
  { label: string; color: string; icon: ReactNode; hint?: string }
> = {
  // 图标承担的是「扫读」：一列几十行状态，形状比颜色先被看到，也比颜色更早区分开
  // ——四个状态里 default 与 processing 的底色在小面积下很接近。
  // PROCESSING 的 spin 是唯一在动的东西，用它表达「还在跑」，不必读文字。
  QUEUED: {
    label: '排队中',
    color: 'default',
    icon: <ClockCircleOutlined />,
    hint: '等前一个导入结束后自动开始',
  },
  PROCESSING: { label: '处理中', color: 'processing', icon: <SyncOutlined spin /> },
  COMPLETED: { label: '已完成', color: 'success', icon: <CheckCircleOutlined /> },
  FAILED: { label: '失败', color: 'error', icon: <CloseCircleOutlined /> },
};

/** 状态码 → 中文名。未知状态码原样返回，不吞掉。 */
export function statusLabel(status: string) {
  return STATUS_META[status]?.label ?? status;
}

/** 状态下拉/筛选用的选项。顺序按导入生命周期，不按字母序。 */
export const STATUS_OPTIONS = ['QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED'].map((value) => ({
  value,
  label: STATUS_META[value].label,
}));

export interface ImportStatusCellProps {
  status: string;
  /** 失败原因，或成功时的备注（跳过行数等）。 */
  errorMessage?: string;
  /** 失败原因过长时截断为几行；不传则不截断（看板用）。 */
  ellipsisRows?: number;
}

/**
 * 一格状态：Tag + 其下的补充说明。
 *
 * 失败时说明是错误信息，其余状态是该状态自带的提示。历史屏不提供详情视图，失败原因
 * 若在列表里读不到，用户就无从判断该不该重导——所以这一格不能只有一个 Tag。
 */
export function ImportStatusCell({ status, errorMessage, ellipsisRows }: ImportStatusCellProps) {
  const meta = STATUS_META[status];
  // 未知状态码原样显示，不吞掉——吞掉会让新状态在界面上表现为「这一格空了」
  const note = status === 'FAILED' ? errorMessage : meta?.hint;
  const clamp = Boolean(ellipsisRows);
  return (
    // Space 默认是 inline-flex，宽度由内容撑开。不给 width:100% + minWidth:0，
    // 里面那行 ellipsis 文本永远不会截断：flex 子项不会收缩到内容宽度以下，
    // CSS 的 text-overflow 于是无从生效——实测一条 SQL 报错把 200px 的单元格撑到
    // 2180px，横向压过后面两列。这两个样式是截断能成立的前提，不是装饰。
    <Space direction="vertical" size={0} style={{ width: '100%', minWidth: 0 }}>
      {/* 未知状态码没有图标，只出文字——凭空给个图标等于替一个我们不认识的状态下判断 */}
      <Tag color={meta?.color ?? 'default'} icon={meta?.icon} style={{ marginInlineEnd: 0 }}>
        {meta?.label ?? status}
      </Tag>
      {note ? (
        <Text
          type="secondary"
          style={{ fontSize: 12, width: '100%', ...(clamp ? { display: 'block' } : null) }}
          ellipsis={clamp ? { tooltip: note } : false}
        >
          {note}
        </Text>
      ) : null}
    </Space>
  );
}
