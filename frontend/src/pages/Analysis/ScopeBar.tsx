import { Button, Input, Select, Space, Tag, Typography } from 'antd';
import type { SiteOption } from '@/services/upload';
import { detectSearchKind, type ScopeDraft } from './filters';
import { formatSiteLabel } from './siteLabel';
import { formatScopeDateLabel } from './scopeDateLabel';
import './ScopeBar.less';

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
    <div className="scope-bar">
      <Space.Compact className="scope-bar__query">
        <Select
          className="scope-bar__site"
          value={scope.site}
          placeholder="站点"
          onChange={(site) => onScopeChange({ ...scope, site })}
          options={sites.map((site) => ({ label: formatSiteLabel(site), value: site.code }))}
        />
        <Select
          className="scope-bar__view"
          value={scope.view}
          onChange={(view) => onScopeChange({ ...scope, view })}
          options={[
            { label: '按天', value: 'DAY' },
            { label: '按周', value: 'WEEK' },
          ]}
        />
        <Select
          className="scope-bar__date"
          // 下拉宽度由选项内容决定，不随触发器：触发器改为自适应后会变窄，若下拉仍与它
          // 同宽，选项就会被截断——而用户正是在这个列表里挑日期。两者必须一起改。
          popupMatchSelectWidth={false}
          value={scope.date ?? undefined}
          placeholder="无可用日期"
          onChange={(date) => onScopeChange({ ...scope, date })}
          options={availableDates.map((date) => ({
            label: formatScopeDateLabel(date, scope.view),
            value: date,
          }))}
          notFoundContent="该站点该视图暂无数据"
        />
        <Input
          className="scope-bar__search"
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
        <Button className="scope-bar__submit" type="primary" loading={loading} onClick={onSubmit}>
          立即查询
        </Button>
      </Space.Compact>
      {/* 显式触发的代价是可能对着新条件读旧数据，这个提示是那份代价的必要补偿 */}
      {dirty && (
        <div className="scope-bar__dirty">
          <Tag color="orange">存在未生效条件，请点击对应的查询或筛选按钮</Tag>
        </div>
      )}
    </div>
  );
}
