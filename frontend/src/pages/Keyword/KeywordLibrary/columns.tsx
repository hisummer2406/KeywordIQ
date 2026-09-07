import type { ProColumns } from '@ant-design/pro-components';
import { Tag } from 'antd';
import { buildColumns as buildAnalysisColumns, totalColumnWidth } from '@/pages/Analysis/columns';
import type { AnalysisRow, View } from '@/services/analysis';
import type { LibraryRowStatus, LibraryWatchRow } from '@/services/library';

/**
 * 盯盘表格的列 = 分析屏那一整套 + 最前面一列「状态」。
 *
 * 指标列**复用**分析屏的 `buildColumns`，不另抄一份：同一个词在两屏必须读到同一套口径，
 * 而两份平行的列定义分叉时不会报错，只会让两屏显示不同的东西。
 *
 * 状态列是这一屏独有的，且**不可省**：分析屏的行集合由事实表决定，那里不存在"该期无数据"
 * 这种行；这里的行集合是用户的清单，无数据的行只会渲染成一个「—」，与"排名恰好为空"
 * 长得一样。掉榜在分析屏已有标签（现排名列），这里仍在状态列重述一次，让三种状态在
 * 同一列里可比较。
 */

const STATUS_META: Record<LibraryRowStatus, { color: string; text: string; hint?: string }> = {
  LISTED: { color: 'green', text: '在榜' },
  DROPPED: { color: 'default', text: '已掉榜' },
  MISSING: { color: 'warning', text: '该期无数据', hint: '这个词在所选期次没有任何数据' },
};

function renderStatus(row: LibraryWatchRow) {
  const meta = STATUS_META[row.status] ?? STATUS_META.MISSING;
  return (
    <span title={meta.hint}>
      <Tag color={meta.color}>{meta.text}</Tag>
    </span>
  );
}

export const STATUS_COLUMN_WIDTH = 110;

/**
 * 复选框列的宽度。它不在 `columns` 里，但照样占横向空间，所以必须同时出现在两处：
 * 这里算进 `scroll.x`，以及 `rowSelection.columnWidth`。只给一处，两边对不上。
 */
export const SELECTION_COLUMN_WIDTH = 48;

/**
 * 表格的 `scroll.x`：分析屏那套列的总宽 + 状态列 + 复选框列。
 *
 * 漏算后两列的表现不是报错，而是最右边那一列被挤扁。
 *
 * （沿用分析屏的做法。实测这个 antd 版本下表格实际仍是 `table-layout: auto`，列宽
 * 只是建议值——那是既有行为，两屏一致，不在本变更范围内。）
 */
export function watchTableWidth(view: View): number {
  return totalColumnWidth(view) + STATUS_COLUMN_WIDTH + SELECTION_COLUMN_WIDTH;
}

export function buildWatchColumns(
  view: View,
  siteDomain?: string,
  onOpenTrend?: (row: AnalysisRow) => void,
): ProColumns<LibraryWatchRow>[] {
  const statusColumn: ProColumns<LibraryWatchRow> = {
    title: '状态',
    dataIndex: 'status',
    width: STATUS_COLUMN_WIDTH,
    align: 'center',
    render: (_, row) => renderStatus(row),
  };
  return [
    statusColumn,
    ...(buildAnalysisColumns(view, siteDomain, onOpenTrend) as ProColumns<LibraryWatchRow>[]),
  ];
}
