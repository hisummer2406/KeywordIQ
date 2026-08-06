import type { Include, SearchBody, View } from '@/services/analysis';

/**
 * 分析查询屏的条件模型与转换。
 *
 * 这里是整块改造中唯一有分支逻辑的部分（检索目标判定、档位映射、空值裁剪），
 * 做成纯函数好让它能被直接测——不用挂 DOM、不用 mock 请求。组件那边只剩渲染
 * 与事件传递。
 */

/** 作用域三元组：决定「查哪一份数据切片」。 */
export interface ScopeDraft {
  site?: string;
  view: View;
  date?: string | null;
}

/** 数值区间。两端各自可空——只填一端是合法的（「1000 名以后」）。 */
export interface Range {
  min?: number;
  max?: number;
}

/**
 * 过滤条件：决定「在切片内筛哪些行」。
 *
 * 不含关键词与 ASIN——那两个维度归作用域区的单一检索框（`q`）。一个维度两个入口时，
 * 两处填了不同的值，用户无从判断哪一处生效。
 */
export interface FilterDraft {
  /** 单一检索框的原文，目标字段由 detectSearchKind 判定 */
  q: string;
  categories: string[];
  ranking: Range;
  change: Range;
  clickShare: Range;
  conversionShare: Range;
  /** undefined = 全部 */
  isNew?: boolean;
  /** 品牌精确多选（前三任一命中），与 categories 对称 */
  brands: string[];
  productTitle: string;
  /**
   * 行集合维度，缺省 ALL。**不是筛选条件**——它决定"哪些行进表"，而筛选决定
   * "进表的行里留哪些"。放在同一个草稿里是因为它和筛选一起被「立即查询」提交。
   */
  include?: Include;
}

export const emptyFilters = (): FilterDraft => ({
  q: '',
  categories: [],
  ranking: {},
  change: {},
  clickShare: {},
  conversionShare: {},
  isNew: undefined,
  brands: [],
  productTitle: '',
});

// --------------------------------------------------------------------------
// 检索目标判定
// --------------------------------------------------------------------------

export type SearchKind = 'asin' | 'keyword' | 'none';

/** 现役 ASIN 形如 B0 + 8 位字母数字。 */
const ASIN_RE = /^B0[A-Z0-9]{8}$/i;

/**
 * 单一检索框输入 → 检索目标。
 *
 * 不走后端 `keyword ILIKE ... OR asin ILIKE ...`：OR 跨两张维度表的两个 trgm 索引，
 * 计划器通常退化为全切片扫描。格式判定在这里一行正则解决。
 *
 * 判定结果必须回显到界面上——自动判定一旦不可见，用户搜不到东西时无从知道
 * 系统搜的是哪个字段。
 */
export function detectSearchKind(input: string): SearchKind {
  const v = input.trim();
  if (!v) return 'none';
  return ASIN_RE.test(v) ? 'asin' : 'keyword';
}

// --------------------------------------------------------------------------
// 快捷档位
// --------------------------------------------------------------------------

export interface RangePreset {
  label: string;
  /** 「全部」档两端皆空，等同于清除该维度 */
  value: Range;
}

/** 档位是区间值的一组预设写入方式，区间值本身才是唯一事实来源。 */
export const RANGE_PRESETS = {
  ranking: [
    { label: '全部', value: {} },
    { label: '1~100', value: { min: 1, max: 100 } },
    { label: '100~1000', value: { min: 100, max: 1000 } },
    { label: '1000~1万', value: { min: 1000, max: 10000 } },
    { label: '1万~10万', value: { min: 10000, max: 100000 } },
    { label: '10万以上', value: { min: 100000 } },
  ],
  // 排名变化为负表示名次数字变小、即排名上升。手输区间用原始数值（与表格
  // 「排名变化」列一致），档位用中文语义承担可读性——界面上翻转符号会让用户
  // 拿表格里看到的数字回填筛选时得到相反的结果。
  change: [
    { label: '全部', value: {} },
    { label: '上升', value: { max: -1 } },
    { label: '大幅上升', value: { max: -100 } },
    { label: '下降', value: { min: 1 } },
    { label: '持平', value: { min: 0, max: 0 } },
  ],
  // 份额库里就是 0~100 的百分数（Numeric(10,2)），界面、档位、接口一路都是百分数，
  // 不引入换算——换算点是最容易单向遗漏的地方，而这里没有引入它的理由。
  share: [
    { label: '全部', value: {} },
    { label: '0~10%', value: { min: 0, max: 10 } },
    { label: '10~20%', value: { min: 10, max: 20 } },
    { label: '20~30%', value: { min: 20, max: 30 } },
    { label: '30~50%', value: { min: 30, max: 50 } },
    { label: '50% 以上', value: { min: 50 } },
  ],
} satisfies Record<string, RangePreset[]>;

/** 某个区间当前是否正好等于某个档位（用于档位高亮）。手输改写后自然不再相等。 */
export function matchedPreset(range: Range, presets: readonly RangePreset[]): string | undefined {
  return presets.find((p) => p.value.min === range.min && p.value.max === range.max)?.label;
}

// --------------------------------------------------------------------------
// 转换为请求体
// --------------------------------------------------------------------------

const trimmed = (s: string) => (s.trim() ? s.trim() : undefined);

/**
 * 草稿条件 + 作用域 → 查询请求体。只带非空字段。
 *
 * 不含分页与排序：导出直接复用它，导的是整个作用域下的这组条件而非当前页。
 */
export function toSearchBody(scope: ScopeDraft, filters: FilterDraft): SearchBody {
  const kind = detectSearchKind(filters.q);
  const q = filters.q.trim();

  // 「仅掉榜」时不带任何其余筛选。筛选一律以锚定期那一行为准，而掉榜行在锚定期没有行
  // 可匹配——带上去的必然结果是一张空表，且用户看不出是哪一步造成的。界面上这些控件
  // 同时置灰（FilterPanel），此处是同一条规则在数据侧的落实：**只靠界面置灰不够**，
  // 草稿里的旧值会在切换开关后残留并被提交。
  const onlyDropped = filters.include === 'DROPPED';

  return {
    site: scope.site,
    view: scope.view,
    date: scope.date ?? undefined,
    include: filters.include && filters.include !== 'ALL' ? filters.include : undefined,
    keyword: !onlyDropped && kind === 'keyword' ? q : undefined,
    asin: !onlyDropped && kind === 'asin' ? q : undefined,
    brands: !onlyDropped && filters.brands.length ? filters.brands : undefined,
    product_title: onlyDropped ? undefined : trimmed(filters.productTitle),
    categories: !onlyDropped && filters.categories.length ? filters.categories : undefined,
    ranking_min: onlyDropped ? undefined : filters.ranking.min,
    ranking_max: onlyDropped ? undefined : filters.ranking.max,
    change_min: onlyDropped ? undefined : filters.change.min,
    change_max: onlyDropped ? undefined : filters.change.max,
    click_share_min: onlyDropped ? undefined : filters.clickShare.min,
    click_share_max: onlyDropped ? undefined : filters.clickShare.max,
    conversion_share_min: onlyDropped ? undefined : filters.conversionShare.min,
    conversion_share_max: onlyDropped ? undefined : filters.conversionShare.max,
    is_new: onlyDropped ? undefined : filters.isNew,
  };
}
