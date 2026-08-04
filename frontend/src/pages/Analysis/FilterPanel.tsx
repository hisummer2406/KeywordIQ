import { Button, Input, InputNumber, Radio, Space, Tooltip, Typography } from 'antd';
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

const LABEL_WIDTH = 88;

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
function RangeRow({ label, hint, suffix, presets, value, onChange }: RangeRowProps) {
  const active = matchedPreset(value, presets);
  return (
    <Row label={label} hint={hint}>
      <Space size={4}>
        <InputNumber
          size="middle"
          style={{ width: 100 }}
          placeholder="最小值"
          suffix={suffix}
          value={value.min ?? null}
          onChange={(v) => onChange({ ...value, min: v ?? undefined })}
        />
        <span style={{ color: '#bfbfbf' }}>~</span>
        <InputNumber
          size="middle"
          style={{ width: 100 }}
          placeholder="最大值"
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

  return (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <RangeRow
        label="搜索排名"
        presets={RANGE_PRESETS.ranking}
        value={value.ranking}
        onChange={(ranking) => patch({ ranking })}
      />
      <RangeRow
        label="排名变化"
        presets={RANGE_PRESETS.change}
        value={value.change}
        onChange={(change) => patch({ change })}
      />
      <RangeRow
        label="点击份额"
        hint={TOP1_ONLY_HINT}
        suffix="%"
        presets={RANGE_PRESETS.share}
        value={value.clickShare}
        onChange={(clickShare) => patch({ clickShare })}
      />
      <RangeRow
        label="转化份额"
        hint={TOP1_ONLY_HINT}
        suffix="%"
        presets={RANGE_PRESETS.share}
        value={value.conversionShare}
        onChange={(conversionShare) => patch({ conversionShare })}
      />
      <Row label="新词">
        <Radio.Group
          size="middle"
          optionType="button"
          value={value.isNew === undefined ? 'all' : String(value.isNew)}
          onChange={(e) =>
            patch({ isNew: e.target.value === 'all' ? undefined : e.target.value === 'true' })
          }
          options={[
            { label: '全部', value: 'all' },
            { label: '新词', value: 'true' },
            { label: '非新词', value: 'false' },
          ]}
        />
      </Row>
      <Row label="商品标题" hint={TOP1_ONLY_HINT}>
        <Input
          size="middle"
          allowClear
          style={{ width: 320 }}
          placeholder="标题关键字"
          value={value.productTitle}
          onChange={(e) => patch({ productTitle: e.target.value })}
        />
      </Row>
    </Space>
  );
}
