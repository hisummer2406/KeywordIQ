import { Button, Input, Select, Space, Tag, Tooltip, Typography } from 'antd';
import type { SiteOption } from '@/services/upload';
import { searchInputError, splitAsins, type ScopeDraft, type SearchMode } from './filters';
import { formatSiteLabel } from './siteLabel';
import { formatScopeDateLabel } from './scopeDateLabel';
import './ScopeBar.less';

/**
 * 作用域栏：站点 / 视图 / 日期 / 搜索方式 / 检索框 / 立即查询，桌面宽度下同一行。
 *
 * 这一行决定「查哪一份数据切片」，与下面的过滤区（「筛哪些行」）性质不同。
 * 受控组件：自身不持状态、不发请求。
 */

// 选项文案即说明：方式是显式选的（#150），用户必须一眼看出当前搜的是哪个字段
const MODE_OPTIONS: { label: string; value: SearchMode; title: string }[] = [
  {
    label: '关键词 · 全部词',
    value: 'ALL_TERMS',
    title:
      '输入的每个词都要作为完整单词出现，顺序不限、可夹其他词，英语常规单复数（rug/rugs、berry/berries）互通，不含不规则变化',
  },
  { label: '关键词 · 词组', value: 'PHRASE', title: '关键词中按原样连续包含输入文本' },
  {
    label: 'ASIN · 批量',
    value: 'ASIN',
    title: '最多 100 个，空格/逗号/分号/换行分隔，可直接从 Excel 粘贴；前三商品任一命中即展示',
  },
];

const PLACEHOLDER: Record<SearchMode, string> = {
  ALL_TERMS: '输入关键词，如 iphone charger',
  PHRASE: '输入关键词，如 iphone charger',
  ASIN: '输入或粘贴 ASIN，空格/逗号/换行分隔，最多 100 个',
};

export interface ScopeBarProps {
  sites: SiteOption[];
  availableDates: string[];
  scope: ScopeDraft;
  /** 检索框原文（属于过滤草稿，但输入位置在这一行） */
  q: string;
  /** 搜索方式（草稿），决定检索框内容按关键词还是 ASIN 查 */
  qMode?: SearchMode;
  /** 草稿与生效条件不一致——存在未生效的改动 */
  dirty: boolean;
  /**
   * 本次进屏是否按用户保存的条件初始化。
   *
   * 不标出来的话，用户无从判断眼前这组条件是自己保存的还是系统给的，也就不知道保存
   * 到底生效没有——保存项因形状过期或站点下线被丢弃时尤其如此。
   */
  savedApplied?: boolean;
  loading?: boolean;
  onScopeChange: (next: ScopeDraft) => void;
  onQChange: (next: string) => void;
  onQModeChange: (next: SearchMode) => void;
  onSubmit: () => void;
}

export default function ScopeBar({
  sites,
  availableDates,
  scope,
  q,
  qMode = 'ALL_TERMS',
  dirty,
  savedApplied,
  loading,
  onScopeChange,
  onQChange,
  onQModeChange,
  onSubmit,
}: ScopeBarProps) {
  const inputError = searchInputError(q, qMode);
  const asinCount = qMode === 'ASIN' ? splitAsins(q).length : 0;
  const submit = () => {
    if (!inputError) onSubmit();
  };

  return (
    <div className="scope-bar">
      <Space.Compact className="scope-bar__query">
        <Select
          className="scope-bar__site"
          // 触发器按内容收窄后，下拉若仍与它同宽会截断较长的站点名
          popupMatchSelectWidth={false}
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
        <Select
          className="scope-bar__mode"
          aria-label="搜索方式"
          // 下拉按选项内容定宽，触发器收窄后选项文字不被截断
          popupMatchSelectWidth={false}
          value={qMode}
          onChange={onQModeChange}
          options={MODE_OPTIONS}
        />
        <Input
          className="scope-bar__search"
          aria-label="搜索内容"
          allowClear
          status={inputError ? 'error' : undefined}
          value={q}
          placeholder={PLACEHOLDER[qMode]}
          onChange={(e) => onQChange(e.target.value)}
          onPressEnter={submit}
          onPaste={(e) => {
            // 单行 input 粘贴时会直接删掉换行，Excel 多行就粘成了一个长串。ASIN 方式下先把
            // 换行/制表符换成空格再插入，保住编号边界；关键词方式不拦，保持原生行为。
            if (qMode !== 'ASIN') return;
            const pasted = e.clipboardData.getData('text');
            if (!/[\r\n\t]/.test(pasted)) return;
            e.preventDefault();
            const input = e.currentTarget;
            const start = input.selectionStart ?? q.length;
            const end = input.selectionEnd ?? q.length;
            const cleaned = pasted.replace(/[\r\n\t]+/g, ' ').trim();
            onQChange([q.slice(0, start), cleaned, q.slice(end)].filter(Boolean).join(' '));
          }}
          suffix={
            qMode === 'ASIN' && asinCount > 0 ? (
              <Typography.Text
                className="scope-bar__count"
                type={inputError ? 'danger' : 'secondary'}
              >
                {inputError ?? `${asinCount} 个`}
              </Typography.Text>
            ) : undefined
          }
        />
        <Button
          className="scope-bar__submit"
          type="primary"
          loading={loading}
          disabled={!!inputError}
          onClick={submit}
        >
          立即查询
        </Button>
      </Space.Compact>
      {/* 显式触发的代价是可能对着新条件读旧数据，这个提示是那份代价的必要补偿 */}
      {savedApplied && (
        <div className="scope-bar__saved">
          <Tooltip title="这组条件来自你保存的默认。点过滤区的「重置条件」可以清除它">
            <Tag color="blue">我的默认</Tag>
          </Tooltip>
        </div>
      )}
      {dirty && (
        <div className="scope-bar__dirty">
          <Tag color="orange">存在未生效条件，请点击对应的查询或筛选按钮</Tag>
        </div>
      )}
    </div>
  );
}
