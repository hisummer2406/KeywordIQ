import { Button, Input, InputNumber, Radio, Space, Switch, Tooltip, Typography } from 'antd';
import {
  RANGE_PRESETS,
  matchedPreset,
  type FilterDraft,
  type Range,
  type RangePreset,
} from './filters';

/**
 * 结果过滤面板（过滤区右栏）。
 *
 * 不含关键词与 ASIN 输入——那两个维度归作用域区的检索框。一个维度两个入口时，
 * 两处填了不同的值，用户无从判断哪一处生效。
 *
 * 受控组件：只认条件对象，不知道当前站点是什么。
 */

/**
 * 条件标签列的宽度。**导出供本区标题复用**——标题与标签共用同一条右对齐线才算"放在一起"，
 * 两处各写一个数值迟早会分叉，而分叉的表现就是标题又飘开。
 */
export const LABEL_WIDTH = 88;

/**
 * 商品维度条件的口径提示。
 *
 * 表格展示点击量前三，而 ASIN 与商品标题这两个条件只匹配第一名——不标注的后果是具体且
 * 可预期的：用户会拿卡牌里第二名的 ASIN 回填筛选，得到空结果，并把它当成数据缺失而非
 * 口径差异。提示挂在填写的位置而不是表格列头，因为误用正发生在填写的那一刻。
 *
 * **只用于 ASIN 与商品标题。** 品牌与类目已改为前三任一命中，展示与筛选对齐，再标注就是
 * 一句描述不存在差异的噪音——而噪音会让用户不再阅读界面提示，连带这两条真正有效的也失效。
 */
export const TOP1_ONLY_HINT = '只匹配点击量第一名的商品；表格里展示的是前三名';

const EXCLUDE_BRAND_HINT =
  '排除关键词里带有该词自己前三品牌名（整词、不分大小写）的词。仅在「只看首次入榜」时生效，默认开启；关闭后首次入榜榜单会包含品牌词。';

function Row({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  const text = (
    <Typography.Text
      type="secondary"
      style={{
        width: LABEL_WIDTH,
        textAlign: 'right',
        flexShrink: 0,
        ...(hint ? { borderBottom: '1px dashed #bfbfbf', cursor: 'help' } : null),
      }}
    >
      {label}
    </Typography.Text>
  );
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
      {hint ? <Tooltip title={hint}>{text}</Tooltip> : text}
      {children}
    </div>
  );
}

interface RangeRowProps {
  label: string;
  hint?: string;
  disabled?: boolean;
  suffix?: string;
  presets: readonly RangePreset[];
  value: Range;
  onChange: (next: Range) => void;
}

/**
 * 一行区间：手输上下界 + 快捷档位。
 *
 * 档位是区间值的一组预设写入方式，区间值本身才是唯一事实来源——所以档位高亮由
 * 区间值反推（matchedPreset），手输改写一端后高亮自然消失，不需要额外的选中状态。
 */
function RangeRow({ label, hint, disabled, suffix, presets, value, onChange }: RangeRowProps) {
  const active = matchedPreset(value, presets);
  return (
    <Row label={label} hint={hint}>
      <Space size={4}>
        <InputNumber
          size="middle"
          style={{ width: 100 }}
          placeholder="最小值"
          disabled={disabled}
          suffix={suffix}
          value={value.min ?? null}
          onChange={(v) => onChange({ ...value, min: v ?? undefined })}
        />
        <span style={{ color: '#bfbfbf' }}>~</span>
        <InputNumber
          size="middle"
          style={{ width: 100 }}
          placeholder="最大值"
          disabled={disabled}
          suffix={suffix}
          value={value.max ?? null}
          onChange={(v) => onChange({ ...value, max: v ?? undefined })}
        />
      </Space>
      <Space size={4} wrap>
        {presets.map((p) => (
          <Button
            key={p.label}
            size="middle"
            disabled={disabled}
            type={active === p.label ? 'primary' : 'default'}
            onClick={() => onChange({ ...p.value })}
          >
            {p.label}
          </Button>
        ))}
      </Space>
    </Row>
  );
}

export interface FilterPanelProps {
  value: FilterDraft;
  onChange: (next: FilterDraft) => void;
}

export default function FilterPanel({ value, onChange }: FilterPanelProps) {
  const patch = (p: Partial<FilterDraft>) => onChange({ ...value, ...p });
  // 「仅掉榜」时其余筛选全部失效：筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行
  // 可匹配，任何条件一生效结果必然为空。置灰是把这条规则摆到用户眼前，免得他填了条件、
  // 得到空表、再去猜是数据没有还是筛错了。数据侧的落实在 filters.ts（草稿旧值不提交）。
  const onlyDropped = value.include === 'DROPPED';

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Row label="行集合">
        <Space size={12} wrap>
          <Radio.Group
            size="middle"
            optionType="button"
            value={value.include ?? 'ALL'}
            onChange={(e) => patch({ include: e.target.value })}
            options={[
              { label: '全部', value: 'ALL' },
              { label: '仅在榜', value: 'LISTED' },
              { label: '仅掉榜', value: 'DROPPED' },
            ]}
          />
          {onlyDropped ? (
            <Typography.Text type="warning">
              掉榜行在所选日期没有数据，无法参与下列筛选——已全部停用
            </Typography.Text>
          ) : null}
        </Space>
      </Row>
      <RangeRow
        label="搜索排名"
        disabled={onlyDropped}
        presets={RANGE_PRESETS.ranking}
        value={value.ranking}
        onChange={(ranking) => patch({ ranking })}
      />
      <RangeRow
        label="排名变化"
        disabled={onlyDropped}
        presets={RANGE_PRESETS.change}
        value={value.change}
        onChange={(change) => patch({ change })}
      />
      <RangeRow
        label="点击份额"
        hint={TOP1_ONLY_HINT}
        disabled={onlyDropped}
        suffix="%"
        presets={RANGE_PRESETS.share}
        value={value.clickShare}
        onChange={(clickShare) => patch({ clickShare })}
      />
      <RangeRow
        label="转化份额"
        hint={TOP1_ONLY_HINT}
        disabled={onlyDropped}
        suffix="%"
        presets={RANGE_PRESETS.share}
        value={value.conversionShare}
        onChange={(conversionShare) => patch({ conversionShare })}
      />
      <Row label="首次入榜">
        <Radio.Group
          size="middle"
          optionType="button"
          disabled={onlyDropped}
          value={value.firstListed ? 'true' : 'all'}
          onChange={(e) =>
            patch({ firstListed: e.target.value === 'true' ? true : undefined })
          }
          options={[
            { label: '全部', value: 'all' },
            { label: '只看首次入榜', value: 'true' },
          ]}
        />
      </Row>
      <Row label="排除品牌词" hint={EXCLUDE_BRAND_HINT}>
        <Switch
          aria-label="排除品牌词"
          disabled={onlyDropped || value.firstListed !== true}
          checked={value.excludeBrandWords !== false}
          onChange={(checked) => patch({ excludeBrandWords: checked })}
        />
      </Row>
      <Row label="商品标题" hint={TOP1_ONLY_HINT}>
        <Input
          size="middle"
          allowClear
          disabled={onlyDropped}
          style={{ width: 320 }}
          placeholder="标题关键字"
          value={value.productTitle}
          onChange={(e) => patch({ productTitle: e.target.value })}
        />
      </Row>
    </Space>
  );
}
