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

/**
 * 「竞对新发现」的口径提示。
 *
 * **必须把 7 天窗口写进提示里。** 空结果有两个同样常见的成因——插件客户端尚未上线
 * （issue #56 P3b，数据根本没进来），以及关注卖家的这些商品是 7 天前就见到的（数据
 * 进来了但都过了窗口）。提示只说前者，用户在插件上线后碰到空表会照着一条已经不成立的
 * 原因去排查（PR #66 三次复审 P2）。
 *
 * 措辞用「发现」不用「上新」：被动采集证明不了商品何时上架，只能证明我们何时见到
 * ——与卖家清单「近 N 日新发现」同一条纪律（见 CLAUDE.md 竞对 ASIN 上报一节）。
 */
export const COMPETITOR_NEW_HINT =
  '近 7 日内首次见到、且晚于该卖家基线批次的商品；数据依赖浏览器插件采集，插件未上线前可能长期无命中';

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
  /**
   * 是否显示「竞对新发现」开关。它读取的是竞对监控域的私有数据（关注了哪些卖家），
   * `analysis:view` 不足以授权——只有同时拥有 `competitor:view` 的用户才看得到这个
   * 开关，后端 `/search`、`/export` 在 `competitor_new=true` 时同样会再校验一次
   * （PR #66 复审 P1：这不是"前端隐藏就够了"的纯展示问题，两处都要拦）。
   */
  canCompetitorNew: boolean;
}

export default function FilterPanel({ value, onChange, canCompetitorNew }: FilterPanelProps) {
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
      <Row label="新词">
        <Radio.Group
          size="middle"
          optionType="button"
          disabled={onlyDropped}
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
      {canCompetitorNew ? (
        <Row label="竞对新发现" hint={COMPETITOR_NEW_HINT}>
          <Switch
            aria-label="竞对新发现"
            disabled={onlyDropped}
            checked={value.competitorNew}
            onChange={(checked) => patch({ competitorNew: checked })}
          />
        </Row>
      ) : null}
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
