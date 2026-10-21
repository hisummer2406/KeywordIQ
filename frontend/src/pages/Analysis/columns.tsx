import { BarChartOutlined, FacebookOutlined, GoogleOutlined, TikTokOutlined } from '@ant-design/icons';
import type { ProColumns } from '@ant-design/pro-components';
import { Tag } from 'antd';
import type { AnalysisRow, View } from '@/services/analysis';
import TrendSparkline from '@/components/TrendSparkline';
import { TREND_COLORS } from '@/components/trendColors';
import RankedList from './RankedList';
import { amazonSearchUrl } from './amazonSearchUrl';
import { facebookAdLibraryUrl } from './facebookAdLibraryUrl';
import { googleTrendsUrl } from './googleTrendsUrl';
import { tiktokAdLibraryUrl } from './tiktokAdLibraryUrl';
import TopProductCards from './TopProductCards';
import TopslotHistoryPopover from './TopslotHistoryPopover';
import type { VisitedKeywords } from './visitedKeywords';

/** 已访问关键词的颜色：与默认链接蓝色区分明显，白底对比度约 7:1。 */
export const VISITED_COLOR = '#722ed1';

/**
 * 悬浮历史面板需要的作用域三元组（issue #96）：站点、视图、锚定日。
 *
 * **必须来自 `loadedSite`/`loadedView`/`loadedDate`（已完成响应的活状态），不是
 * `appliedRef`（已提交但可能仍在飞的请求）。** 表格当前渲染的行本身就是上一次完成
 * 响应的数据，悬浮历史查的是"这一行现在显示的这个 ASIN/品牌"，必须与行数据同一个
 * 作用域——用 `appliedRef` 会在"新查询已提交、旧行还没被替换"这个短暂窗口里，把
 * 新站点/视图配上旧行的 keyword_id 发出去，多数情况下后端会因 site/keyword_id
 * 不匹配报错，即便凑巧不报错也会读到与当前行不属于同一次查询的数据。三个字段全部
 * 到位才提供悬浮入口，缺一个（还没查询出首屏数据）就不渲染，不给一个注定失败的入口。
 */
export interface HistoryScope {
  site: string;
  view: View;
  on: string;
}

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
 * 类目同样按名次下发（`top1~3_category`），已作为独立一列展示，位置在品牌之前。
 *
 * **「操作」是恒定的最末列**（issue #78 V0：Google 搜索趋势 + Meta/TikTok 广告库外链），
 * 品牌不再是最后一列。
 * 这条置换是刻意的：操作类的列（跳转、后续可能追加的行内按钮）习惯上靠右收尾，新增
 * 操作项时只在这一列内追加，不会像数据列那样牵动"往哪插入不挤走谁"的顺序讨论。
 */

/** 排名变化：数值变小 = 名次上升。0 与空各有各的呈现，不要混。 */
function renderChange(v: number | null | undefined) {
  if (v === null || v === undefined) return <span style={{ color: '#ccc' }}>—</span>;
  if (v === 0) return <span style={{ color: TREND_COLORS.flat }}>0</span>;
  const up = v < 0;
  return (
    <span style={{ color: up ? TREND_COLORS.up : TREND_COLORS.down }}>
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

/** 历史排名标签列的宽度，按最长的「12 周前」取。数字不定宽——定宽会再次制造空隙。 */
const HISTORY_LABEL_WIDTH = 52;

/**
 * 排名变化率。
 *
 * - **周视图**：变化量相对**上期**（上一周）排名的百分比，列名「排名变化率」。
 * - **日视图**：对比 **7 天前**的排名（issue #119），列名「7天变化率」。基期取接口 `history`
 *   的第三位（日视图固定为 昨天 / 3 天前 / 7 天前），不是 `previous_ranking`——后者是昨天，
 *   昨天没出现的词因此也能在 7 天前有过排名时算出变化。
 *
 * 在展示层算而不在库里存：它是已有字段的纯函数，存一份就多一处可能与那些数对不上的地方
 * （事实表只追加，改口径要重导）。
 *
 * 基期为空或为零、当期排名为空时给占位符——除以零会渲染出 Infinity / NaN，用户会把它当成
 * 「变化极大」而不是「无从计算」；也绝不用 0% 顶替，那会读成「没变化」。
 */
function renderChangeRate(row: AnalysisRow, view: View) {
  let change: number | null | undefined;
  let base: number | null | undefined;
  if (view === 'DAY') {
    base = row.history?.[2];
    change =
      row.current_ranking === null || row.current_ranking === undefined || !base
        ? null
        : row.current_ranking - base;
  } else {
    change = row.ranking_change;
    base = row.previous_ranking;
  }
  if (change === null || change === undefined || !base) return PLACEHOLDER;
  if (change === 0) return <span style={{ color: TREND_COLORS.flat }}>0%</span>;
  const up = change < 0;
  const pct = Math.abs((change / base) * 100);
  return (
    <span style={{ color: up ? TREND_COLORS.up : TREND_COLORS.down }}>
      {up ? '↑' : '↓'} {pct.toFixed(1)}%
    </span>
  );
}

/**
 * 现排名单元格：排名数值 + 状态标签。
 *
 * 「首次入榜」与「已掉榜」**互斥**，掉榜优先：一行要么是本期首次进入监测范围的，要么是本期
 * 已经没有的，不可能同时成立。原先「新词」是独立一列，掉榜行在那一列只能显示占位符，白占一列宽度。
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
      {row.first_listed ? <Tag color="gold" style={{ marginInlineStart: 6 }}>首次入榜</Tag> : null}
    </span>
  );
}

/**
 * 表头与单元格内容统一居中。
 *
 * `align` 同时作用于表头与内容，故不再另设 `onHeaderCell`。
 *
 * **注意 `align` 只是给 td 设 `text-align`，对块级的 flex 容器不生效**——历史排名、
 * 商品卡牌、品牌/类目三处的内容都是 flex 布局，它们各自用 `inline-flex` 或
 * `justify-content` 自行居中。只加 `align` 而不管那三处，表头会居中而内容仍然靠左。
 */
/** 内容宽度随行变化的列——它们必须左对齐，居中会让每行起点不同、往下扫时左右横跳。 */
const LEFT_ALIGNED = new Set(['关键词', '历史排名', '点击前三类目', '点击前三品牌']);

function alignColumns(columns: ProColumns<AnalysisRow>[]): ProColumns<AnalysisRow>[] {
  return columns.map((column) => ({
    ...column,
    align: LEFT_ALIGNED.has(String(column.title)) ? ('left' as const) : ('center' as const),
    // 表头一律居中，与内容对齐方式无关：表头是短标签，居中只影响观感；
    // 正文对齐则决定能不能顺着列扫读，两者不该被同一个开关绑住。
    onHeaderCell: () => ({ style: { textAlign: 'center' as const } }),
  }));
}

export function buildColumns(
  view: View,
  siteDomain?: string,
  onOpenTrend?: (row: AnalysisRow) => void,
  siteCode?: string,
  historyScope?: HistoryScope,
  visited?: VisitedKeywords,
): ProColumns<AnalysisRow>[] {
  return alignColumns([
    {
      title: '排名',
      dataIndex: 'current_ranking',
      width: 100,
      render: (_, r) => renderRanking(r),
    },
    // 关键词与商品标题是两列「要读内容」的列，其余自适应列给定宽，把剩余横向空间
    // 让给这两列。
    {
      title: '关键词',
      dataIndex: 'keyword',
      // 定宽 + 换行，不用省略号：搜索词常有十几个单词，省略号会把后半截藏掉，而后半截往往
      // 正是区分同类词的部分（"case for iphone 15 **pro max**"）。换行占的是纵向空间，
      // 而行高已由商品卡牌撑起，多一两行不额外增加表格高度。
      width: 220,
      copyable: true,
      // 关键词点开该站点的亚马逊搜索结果页。域名来自**生效作用域**的站点而非草稿——
      // 用户切了站点但没点查询时，表格里还是上一个站点的数据，链接必须跟着数据走。
      // 拿不到域名（站点列表未加载完）时降级为纯文本，不给一个会跳错的链接。
      render: (_, r) => {
        const url = amazonSearchUrl(r.keyword, siteDomain);
        if (!url) return r.keyword;
        return (
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            title={`在亚马逊搜索「${r.keyword}」`}
            // 点击（含 Ctrl/Cmd 点击与键盘回车，它们都会触发 click）与鼠标中键才算访问；
            // 悬浮、复制关键词、打开详情都不经过这个链接，因此不会误标。
            onClick={() => visited?.markVisited(siteCode, r.keyword)}
            onAuxClick={(e) => {
              if (e.button === 1) visited?.markVisited(siteCode, r.keyword);
            }}
            data-visited={visited?.isVisited(siteCode, r.keyword) ? 'true' : undefined}
            // 长词无空格时也要断得开，否则会把列宽顶破
            style={{
              wordBreak: 'break-word',
              ...(visited?.isVisited(siteCode, r.keyword) ? { color: VISITED_COLOR } : {}),
            }}
          >
            {r.keyword}
          </a>
        );
      },
    },
    {
      title: '搜索趋势',
      dataIndex: 'trend',
      // 加宽后小图同步变宽（见 TrendSparkline 的 width 默认值）：这一列的信息密度取决于
      // 横向像素——七个点的排名标签是交替错开排的，宽度越大同侧标签间距越松。
      width: 240,
      render: (_, r) => {
        const sparkline = <TrendSparkline view={view} values={r.trend} dates={r.trend_dates} />;
        // **整格可点，不只是有折线的部分**：无历史数据（只显示占位符）的行同样要能打开
        // 详情——关键词身份已知就该能查，不该要求这一行先有非空的小图（issue #86）。
        if (!onOpenTrend) return sparkline;
        return (
          <div
            role="button"
            title="查看关键词 ABA 排名趋势详情"
            style={{ cursor: 'pointer' }}
            onClick={() => onOpenTrend(r)}
          >
            {sparkline}
          </div>
        );
      },
    },
    {
      title: '排名变化',
      dataIndex: 'ranking_change',
      width: 100,
      render: (_, r) => renderChange(r.ranking_change),
    },
    {
      title: view === 'DAY' ? '7天变化率' : '排名变化率',
      dataIndex: 'ranking_change',
      width: 110,
      render: (_, r) => renderChangeRate(r, view),
    },
    // 单列三行：三个参照点同列纵排，读的是"这个词一路怎么变的"。缺期显示占位符而**不**
    // 隐藏该行——隐藏会让三个点错位，用户读到的「4 周前」其实是「12 周前」的值。
    //
    // 标签**右对齐**、数字**左对齐**，两边都朝中间靠——这是这一格唯一不留大空隙的排法。
    //
    // 踩过两次的坑，写下来免得再改回去：
    // 1. 数字用 `flex:1` 撑满 → 被顶到 130px 列的最右端，标签只有「昨天」两字时空出约 60px。
    // 2. 改成两段定宽、但都朝外对齐 → 标签在 52px 盒里左对齐右边空 28px，数字在 52px 盒里
    //    右对齐左边又空 32px，**空隙加了两遍**共 66px，比第 1 种还糟。
    //
    // 代价是数字不再个位对齐（标签宽度不一：周视图「上周」2 字 vs「12 周前」4 字）。这是
    // 刻意取舍：这三行是同一个词的纵向对照，读的是"一路怎么变的"，紧凑比位数可比更要紧。
    // tabular-nums 保留，至少同位数的行之间仍然齐。
    {
      title: '历史排名',
      dataIndex: 'history',
      width: 140,
      render: (_, r) => (
        <span
          style={{
            // 不居中：块宽随数字位数变化，各行不同，居中后起点会左右跳（同 RankedList）
            display: 'flex',
            flexDirection: 'column',
            gap: 2,
            fontSize: 12,
          }}
        >
          {HISTORY_LABELS[view].map((label, i) => (
            <span key={label} style={{ display: 'flex', gap: 6 }}>
              <span
                style={{
                  width: HISTORY_LABEL_WIDTH,
                  flexShrink: 0,
                  textAlign: 'right',
                  color: '#999',
                }}
              >
                {label}
              </span>
              <span
                data-testid="history-value"
                style={{ textAlign: 'left', fontVariantNumeric: 'tabular-nums' }}
              >
                {r.history?.[i] ?? PLACEHOLDER}
              </span>
            </span>
          ))}
        </span>
      ),
    },
    // 定宽贴合内容：三张卡牌 3×112 + 2×8 = 352px，留 8px 余量。此处原为"不给 width，让它
    // 吃掉所有剩余空间"——但它的内容是固定结构，吃掉的富余全变成卡牌与右邻列之间的空白。
    //
    // 这一格信息密度最高（三张卡 × 图 + ASIN + 两个份额），所以宽裕是靠**把卡牌做大**
    // 换来的（CARD_WIDTH 92→112、缩略图 56→72），不是靠给列加宽——后者只会加宽空白。
    {
      title: '点击前三 ASIN',
      dataIndex: 'top1_asin',
      width: 360,
      render: (_, r) => (
        <TopProductCards row={r} siteDomain={siteDomain} historyScope={historyScope} />
      ),
    },
    // 类目与品牌同为长文本列，各给 180px 并允许换行。**所有列都定宽**是刻意的：只要有
    // 任何一列不定宽，它就会独吞全部横向富余，在它与相邻列之间造出一个显眼的洞——这一点
    // 试过三种分法（卡牌列独吞 / 关键词独吞 / 两列平分），洞只是换位置，没有消失。
    // 全部定宽后，多余宽度由浏览器按比例微调摊给每一列，不再堆在任何一处。
    //
    // **位置必须在品牌之前。**（历史上品牌曾被要求恒在最末，见 analysis-screen-layout；
    // issue #78 引入「操作」列后，恒在最末的换成了操作列，品牌退到倒数第二——但类目仍需
    // 排在品牌之前，这条相对顺序没有变。）columns.test.tsx 会拦住放错位置。
    {
      title: '点击前三类目',
      dataIndex: 'top1_category',
      // 与品牌同为**辅助信息**，不该占宽：两者都是 ellipsis + 悬停看全文，
      // 截断的代价很小，而占宽的代价是挤掉关键词这类真正要读的列。
      width: 130,
      render: (_, r) => (
        <RankedList values={[r.top1_category, r.top2_category, r.top3_category]} rows={1} />
      ),
    },
    // 品牌是辅助信息，读表时先看排名与商品，品牌是回头才需要的那一眼——但它不再是最末列，
    // 「操作」列排在它后面（见文件头注释）。
    {
      title: '点击前三品牌',
      dataIndex: 'top1_brand',
      width: 130,
      render: (_, r) => (
        <RankedList
          values={[r.top1_brand, r.top2_brand, r.top3_brand]}
          wrapValue={
            historyScope
              ? (_i, value, node) => (
                  <TopslotHistoryPopover
                    site={historyScope.site}
                    view={historyScope.view}
                    on={historyScope.on}
                    keywordId={r.keyword_id}
                    kind="BRAND"
                    value={value}
                    label={value}
                  >
                    {node}
                  </TopslotHistoryPopover>
                )
              : undefined
          }
        />
      ),
    },
    // 「操作」是恒定的最末列（issue #78 V0：Google 搜索趋势 + Meta 广告库 + TikTok
    // 广告库）。三项都是**直接新标签页跳转的外链**，不做抽屉/弹窗——那是这一屏在与用户
    // 对齐后砍掉的方案（内嵌趋势图受 X-Frame-Options 限制做不到，摘要抽屉又多一次点击
    // 换不来实质信息，不如直接跳转）。三条链接同一套降级原则：站点码拿不到/不在覆盖范围
    // 时不渲染对应入口，宁可空着，也不给一个打不开或打到错误地区的链接——因此三项各自
    // 独立判空，不共用一次判定。
    //
    // **TikTok 那一项与另外两项体验不同，必须诚实标注**：实测 TikTok 广告库的搜索结果
    // 不支持深链接——URL 能把国家/关键词/日期都预填好，但落地页不会自动查询，显示
    // "0 条"，要用户自己在页面上再点一次 Search 才会出真实结果（详见
    // tiktokAdLibraryUrl.ts）。图标 title 里点破这一点，避免用户把"还没点搜索"
    // 误读成"这个词确实没有广告在投"。
    {
      title: '操作',
      dataIndex: 'operations',
      width: 48,
      render: (_, r) => {
        const trendsUrl = googleTrendsUrl(r.keyword, siteCode);
        const adLibraryUrl = facebookAdLibraryUrl(r.keyword, siteCode);
        const tiktokUrl = tiktokAdLibraryUrl(r.keyword, siteCode);
        // 「历史走势」图标不依赖站点码或该行是否有小图数据——关键词身份已知就该能查
        // （issue #86 已确立的原则），只依赖调用方是否传入了 onOpenTrend。它与「搜索
        // 趋势」小图格打开**同一个**详情面板，不新建第二套图表或查询路径（issue #94）。
        if (!trendsUrl && !adLibraryUrl && !tiktokUrl && !onOpenTrend) return PLACEHOLDER;
        // 上下排列：这一列窄，横向摆图标会挤压相邻列；纵向堆叠不占额外横向空间。
        // 四个图标共用同一个 fontSize（16px），不给任何一个单独加码。
        return (
          <span
            style={{
              display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6, fontSize: 16,
            }}
          >
            {onOpenTrend && (
              <a
                onClick={() => onOpenTrend(r)}
                title="查看关键词 ABA 排名趋势详情（含完整历史）"
                aria-label="查看历史走势"
              >
                <BarChartOutlined />
              </a>
            )}
            {trendsUrl && (
              <a
                href={trendsUrl}
                target="_blank"
                rel="noopener noreferrer"
                title="Google Trends · 最近7天（与报表日期无关）"
                aria-label="查看 Google 搜索趋势"
              >
                <GoogleOutlined />
              </a>
            )}
            {adLibraryUrl && (
              <a
                href={adLibraryUrl}
                target="_blank"
                rel="noopener noreferrer"
                title="在 Meta 广告库中查看该词的竞品投放证据"
                aria-label="查看 Meta 广告库"
              >
                <FacebookOutlined />
              </a>
            )}
            {tiktokUrl && (
              <a
                href={tiktokUrl}
                target="_blank"
                rel="noopener noreferrer"
                title="在 TikTok 广告库中查看该词的竞品投放证据（打开后需要再点一次页面上的 Search 才会出结果）"
                aria-label="查看 TikTok 广告库"
              >
                <TikTokOutlined />
              </a>
            )}
          </span>
        );
      },
    },
  ]);
}

/**
 * 列宽合计，供表格的 `scroll.x` 使用。
 *
 * **列宽真正生效靠的是 `tableLayout="fixed"`，不是 `scroll.x`。** rc-table 升级后
 * 「给了 scroll.x 就切 fixed」不再成立（见 #43），表格退回 auto 布局时 `width` 只是
 * 建议值——浏览器按内容分配，同样写 130px 的类目与品牌会渲染成两个宽度（类目名长，
 * 抢到更多）。两处都要给：`tableLayout` 决定 width 生不生效，`scroll.x` 决定窗口
 * 不够宽时是横向滚动还是把列压扁。
 *
 * 由列定义算出而不是写死：加列或改宽时自动同步，否则总宽与实际列宽对不上。
 */
export function totalColumnWidth(view: View): number {
  return buildColumns(view).reduce((sum, col) => sum + (Number(col.width) || 0), 0);
}
