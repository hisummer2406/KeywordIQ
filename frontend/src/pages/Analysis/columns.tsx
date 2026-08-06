import type { ProColumns } from '@ant-design/pro-components';
import { Tag } from 'antd';
import type { AnalysisRow, View } from '@/services/analysis';
import TrendSparkline from '@/components/TrendSparkline';
import RankedList from './RankedList';
import { amazonSearchUrl } from './amazonSearchUrl';
import TopProductCards from './TopProductCards';

/**
 * 结果表格的列定义。
 *
 * 列上不再挂 `valueType` / `fieldProps` 的搜索配置——筛选已全部移入过滤区，
 * 同一个条件出现在两处会让用户无法判断哪一处生效。
 *
 * 商品信息由「点击前三 ASIN」一列承担，**不再单列** 品牌 / ASIN / 商品标题 / 点击份额 /
 * 转化份额——它们展示的正是卡牌里的第一名，同一个数字在一行里出现两次会让用户误以为是
 * 两个不同的量。
 *
 * 品牌**单独成列**、不进商品卡牌：报表里的「点击量第 N 的品牌」是关键词级的独立排行，
 * 不是「第 N 个商品的品牌」。旧模型把两者硬配后存进 dim_product，同一 ASIN 因此携带
 * 互相冲突的品牌值；keyword-level-brand-category 把它移到了事实表的独立列上，展示
 * 也就必须跟着分开——挂回卡牌里等于把那个配对重新宣称一遍。
 *
 * 类目数据同样已按名次下发（`top1~3_category`），只是暂不展示，需要时照品牌列加回即可。
 */

/** 排名变化：数值变小 = 名次上升。0 与空各有各的呈现，不要混。 */
function renderChange(v: number | null | undefined) {
  if (v === null || v === undefined) return <span style={{ color: '#ccc' }}>—</span>;
  if (v === 0) return <span style={{ color: '#999' }}>0</span>;
  const up = v < 0;
  return (
    <span style={{ color: up ? '#3f8600' : '#cf1322' }}>
      {up ? '↑' : '↓'} {Math.abs(v)}
    </span>
  );
}

/** 历史参照点的标签，按视图取。顺序与接口的 `history` 数组一一对应，不可换序。 */
const HISTORY_LABELS: Record<View, [string, string, string]> = {
  DAY: ['昨天', '3 天前', '7 天前'],
  WEEK: ['上周', '4 周前', '12 周前'],
};

const PLACEHOLDER = <span style={{ color: '#ccc' }}>—</span>;

/**
 * 排名变化率：变化量相对**上期**排名的百分比。
 *
 * 在展示层算而不在库里存：它是 ranking_change 与 previous_ranking 的纯函数，存一份
 * 就多一处可能与那两个数对不上的地方（事实表只追加，改口径要重导）。
 *
 * 上期排名为空或为零时给占位符——除以零会渲染出 Infinity / NaN，而用户会把它当成
 * 「变化极大」而不是「无从计算」。
 */
function renderChangeRate(row: AnalysisRow) {
  const { ranking_change: change, previous_ranking: prev } = row;
  if (change === null || change === undefined || !prev) return PLACEHOLDER;
  if (change === 0) return <span style={{ color: '#999' }}>0%</span>;
  const up = change < 0;
  const pct = Math.abs((change / prev) * 100);
  return (
    <span style={{ color: up ? '#3f8600' : '#cf1322' }}>
      {up ? '↑' : '↓'} {pct.toFixed(1)}%
    </span>
  );
}

/**
 * 现排名单元格：排名数值 + 状态标签。
 *
 * 「新词」与「已掉榜」**互斥**，掉榜优先：一行要么是本期新出现的，要么是本期已经没有的，
 * 不可能同时成立。原先「新词」是独立一列，掉榜行在那一列只能显示占位符，白占一列宽度。
 *
 * 掉榜行没有当期排名（一律为空，不用最后在榜期的值顶替），故显示最后在榜期而非数字。
 */
function renderRanking(row: AnalysisRow) {
  if (row.is_dropped) {
    return (
      <span>
        <Tag color="default">已掉榜</Tag>
        <span style={{ color: '#999', fontSize: 12 }}>{row.last_seen_date ?? '—'}</span>
      </span>
    );
  }
  return (
    <span>
      {row.current_ranking ?? PLACEHOLDER}
      {row.is_new ? <Tag color="gold" style={{ marginInlineStart: 6 }}>新词</Tag> : null}
    </span>
  );
}

export function buildColumns(view: View, siteDomain?: string): ProColumns<AnalysisRow>[] {
  return [
    {
      title: '排名',
      dataIndex: 'current_ranking',
      width: 120,
      render: (_, r) => renderRanking(r),
    },
    // 关键词与商品标题是两列「要读内容」的列，其余自适应列给定宽，把剩余横向空间
    // 让给这两列。
    {
      title: '关键词',
      dataIndex: 'keyword',
      width: 220,
      ellipsis: true,
      copyable: true,
      // 关键词点开该站点的亚马逊搜索结果页。域名来自**生效作用域**的站点而非草稿——
      // 用户切了站点但没点查询时，表格里还是上一个站点的数据，链接必须跟着数据走。
      // 拿不到域名（站点列表未加载完）时降级为纯文本，不给一个会跳错的链接。
      render: (_, r) => {
        const url = amazonSearchUrl(r.keyword, siteDomain);
        if (!url) return r.keyword;
        return (
          <a href={url} target="_blank" rel="noopener noreferrer" title={`在亚马逊搜索「${r.keyword}」`}>
            {r.keyword}
          </a>
        );
      },
    },
    {
      title: '搜索趋势',
      dataIndex: 'trend',
      width: 190,
      render: (_, r) => <TrendSparkline view={view} values={r.trend} dates={r.trend_dates} />,
    },
    {
      title: '排名变化',
      dataIndex: 'ranking_change',
      width: 100,
      render: (_, r) => renderChange(r.ranking_change),
    },
    {
      title: '排名变化率',
      dataIndex: 'ranking_change',
      width: 110,
      render: (_, r) => renderChangeRate(r),
    },
    // 单列三行：三个参照点同列纵排，读的是"这个词一路怎么变的"。缺期显示占位符而**不**
    // 隐藏该行——隐藏会让三个点错位，用户读到的「4 周前」其实是「12 周前」的值。
    {
      title: '历史排名',
      dataIndex: 'history',
      width: 130,
      render: (_, r) => (
        <span style={{ display: 'inline-flex', flexDirection: 'column', gap: 2, fontSize: 12 }}>
          {HISTORY_LABELS[view].map((label, i) => (
            <span key={label}>
              <span style={{ color: '#999' }}>{label}</span>{' '}
              {r.history?.[i] ?? PLACEHOLDER}
            </span>
          ))}
        </span>
      ),
    },
    // 不给 width：让它吃掉所有剩余横向空间。
    {
      title: '点击前三 ASIN',
      dataIndex: 'top1_asin',
      render: (_, r) => <TopProductCards row={r} />,
    },
    // 品牌放在最末列：它是辅助信息，读表时先看排名与商品，品牌是回头才需要的那一眼。
    // 类目列暂不展示（数据仍在接口里，需要时把列加回来即可）。
    {
      title: '点击前三品牌',
      dataIndex: 'top1_brand',
      width: 150,
      render: (_, r) => <RankedList values={[r.top1_brand, r.top2_brand, r.top3_brand]} />,
    },
  ];
}
