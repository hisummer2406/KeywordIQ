import { SearchOutlined } from '@ant-design/icons';
import { Button, Input, Select, Space, Tag, Typography } from 'antd';
import type { SiteOption } from '@/services/upload';
import { detectSearchKind, type ScopeDraft } from './filters';

/**
 * 作用域栏：站点 / 视图 / 日期 / 单一检索框 / 立即查询。
 *
 * 这一行决定「查哪一份数据切片」，与下面的过滤区（「筛哪些行」）性质不同。
 * 受控组件：自身不持状态、不发请求。
 */

const KIND_LABEL = {
  asin: '按 ASIN 检索',
  keyword: '按关键词检索',
  none: '',
} as const;

export interface ScopeBarProps {
  sites: SiteOption[];
  availableDates: string[];
  scope: ScopeDraft;
  /** 检索框原文（属于过滤草稿，但输入位置在这一行） */
  q: string;
  /** 草稿与生效条件不一致——存在未生效的改动 */
  dirty: boolean;
  loading?: boolean;
  onScopeChange: (next: ScopeDraft) => void;
  onQChange: (next: string) => void;
  onSubmit: () => void;
}

export default function ScopeBar({
  sites,
  availableDates,
  scope,
  q,
  dirty,
  loading,
  onScopeChange,
  onQChange,
  onSubmit,
}: ScopeBarProps) {
  const kind = detectSearchKind(q);

  return (
    <Space size="middle" wrap style={{ width: '100%' }}>
      <Select
        style={{ width: 160 }}
        value={scope.site}
        placeholder="站点"
        onChange={(site) => onScopeChange({ ...scope, site })}
        options={sites.map((s) => ({ label: `${s.name_zh} (${s.code})`, value: s.code }))}
      />
      <Select
        style={{ width: 100 }}
        value={scope.view}
        onChange={(view) => onScopeChange({ ...scope, view })}
        options={[
          { label: '按天', value: 'DAY' },
          { label: '按周', value: 'WEEK' },
        ]}
      />
      <Select
        style={{ width: 160 }}
        value={scope.date ?? undefined}
        placeholder="无可用日期"
        onChange={(date) => onScopeChange({ ...scope, date })}
        options={availableDates.map((d) => ({ label: d, value: d }))}
        notFoundContent="该站点该视图暂无数据"
      />
      <Input
        style={{ width: 320 }}
        allowClear
        value={q}
        placeholder="输入 ASIN 或关键词，如 B07Z82895W 或 iphone charger"
        onChange={(e) => onQChange(e.target.value)}
        onPressEnter={onSubmit}
        // 判定是自动的，但不能是不可见的：搜不到东西时用户需要知道系统搜的是哪个字段
        suffix={
          kind === 'none' ? (
            <span />
          ) : (
            <Typography.Text type="secondary" style={{ fontSize: 12, whiteSpace: 'nowrap' }}>
              {KIND_LABEL[kind]}
            </Typography.Text>
          )
        }
      />
      <Button type="primary" icon={<SearchOutlined />} loading={loading} onClick={onSubmit}>
        立即查询
      </Button>
      {/* 显式触发的代价是可能对着新条件读旧数据，这个提示是那份代价的必要补偿 */}
      {dirty && <Tag color="orange">条件已修改，点击立即查询生效</Tag>}
    </Space>
  );
}
