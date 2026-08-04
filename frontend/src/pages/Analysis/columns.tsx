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
 * **没有「点击前三品牌」列**，这是刻意的：报表里的「点击量第 N 的品牌」是关键词级的独立
 * 排行，不是「第 N 个商品的品牌」，而导入侧把它按商品存进了 dim_product——同一 ASIN 在
 * 报表里携带多个互相冲突的品牌值（实测 28.5%），落库时任选其一胜出。在数据模型修好之前
 * 展示它，等于把一个已知错误在每行显示三遍。类目列同理，只保留原有的单列不再扩展。
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

export function buildColumns(view: View, siteDomain?: string): ProColumns<AnalysisRow>[] {
  return [
    { title: '排名', dataIndex: 'current_ranking', width: 80 },
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
      title: '新词',
      dataIndex: 'is_new',
      width: 80,
      render: (_, r) => (r.is_new ? <Tag color="gold">新词</Tag> : '—'),
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
