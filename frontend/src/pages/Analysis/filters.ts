import type { FirstListingMode, Include, KeywordMode, SearchBody, View } from '@/services/analysis';

/**
 * 分析查询屏的条件模型与转换。
 *
 * 这里是整块改造中唯一有分支逻辑的部分（检索方式路由、ASIN 拆分、档位映射、空值裁剪），
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
  /** 单一检索框的原文，目标字段由 qMode 决定 */
  q: string;
  /**
   * 搜索方式：两种关键词匹配方式，或 ASIN 批量。**检索目标只由它决定**，不看输入长什么样
   * （#150：按 `B0…` 格式推断查不了 ISBN-10、也会把 10 位英文词误判成 ASIN）。
   * 缺省视同 ALL_TERMS。不入库（保存的条件不含检索词，方式单独存没有意义）。
   */
  qMode?: SearchMode;
  categories: string[];
  ranking: Range;
  change: Range;
  clickShare: Range;
  conversionShare: Range;
  /**
   * 首次口径（#178），互斥三选一：`undefined` 全部；`'PERIOD'` 首次进入当前榜单（日视图日榜、
   * 周视图周榜）；`'MONITOR'` 首次进入监测（原「只看首次入榜」）。旧快照的 `firstListed: true`
   * 读取时由 `normalizeFirstListing` 转成 `'MONITOR'`，含义不变。
   */
  firstListing?: FirstListingMode;
  /** 品牌精确多选（前三任一命中），与 categories 对称 */
  brands: string[];
  productTitle: string;
  /**
   * 排除关键词里带有自己前三品牌名的词（issue #119）。**缺省视同开启**（`!== false`）：
   * 旧快照没有这个字段，行为应与新进屏一致。只在选了任一首次口径时才随请求发出，见 toSearchBody。
   */
  excludeBrandWords?: boolean;
  /**
   * 行集合维度，缺省 ALL。**不是筛选条件**——它决定"哪些行进表"，而筛选决定
   * "进表的行里留哪些"。放在同一个草稿里是因为它和筛选一起被「立即查询」提交。
   */
  include?: Include;
}

/**
 * 筛选草稿的默认值——**进屏看到的**与**「重置」清成的**是同一份。
 *
 * `firstListing: 'MONITOR'` 是默认值而不是空值：用户进这一屏最常问的第一个问题是「本期
 * 首次入榜了哪些词」，不是「今天的榜单长什么样」。首屏自动查询用的也是这一份，两者必须同源，
 * 否则会出现表格里是首次入榜、而筛选控件显示「全部」的错位。#178 新增的「首次进入当前榜单」
 * **不改这个内置默认**：上线后静默换口径会让所有人的结果范围悄悄变化；需要的用户自己保存。
 *
 * 这个函数从前叫 `emptyFilters`。带上首次筛选之后那个名字与事实相反，而
 * 它正被测试当作「中性基线」使用——名不副实的工厂函数会被下一个人当空值直接用。
 * 要真正的中性值请显式写 `{ ...defaultFilters(), firstListing: undefined }`。
 */
export const defaultFilters = (): FilterDraft => ({
  q: '',
  qMode: 'ALL_TERMS',
  categories: [],
  ranking: {},
  change: {},
  clickShare: {},
  conversionShare: {},
  firstListing: 'MONITOR',
  brands: [],
  productTitle: '',
  excludeBrandWords: true,
});

/**
 * 把旧形状的首次筛选读成新口径（#178）：旧快照/保存条件只有 `firstListed: true`（只看首次入榜，
 * 即共享口径）→ `firstListing: 'MONITOR'`；`firstListed` 一律剔除。已有 `firstListing` 的原样保留。
 *
 * 读取时转换而不提升 `SAVED_MODE_VERSION`：提升版本会把每个用户保存的整组条件丢掉，而 #178 要求
 * 「旧保存条件的含义不变」——这是一个字段到另一个字段的一一映射，不是形状不符。
 */
export function normalizeFirstListing<T extends object>(filters: T): T {
  const legacy = filters as T & { firstListed?: boolean; firstListing?: FirstListingMode };
  if (!('firstListed' in legacy)) return filters;
  const { firstListed, ...rest } = legacy;
  const next = rest as T & { firstListing?: FirstListingMode };
  if (next.firstListing === undefined && firstListed === true) next.firstListing = 'MONITOR';
  return next;
}

/** 首次口径在界面上的名称：「首次进入当前榜单」随视图写成日榜/周榜。 */
export function firstListingLabel(mode: FirstListingMode, view: View): string {
  if (mode === 'MONITOR') return '首次进入监测';
  return view === 'WEEK' ? '首次进入周榜' : '首次进入日榜';
}

// --------------------------------------------------------------------------
// 检索方式与 ASIN 批量输入
// --------------------------------------------------------------------------

/** 检索框的搜索方式：关键词的两种匹配方式，或 ASIN 批量。 */
export type SearchMode = KeywordMode | 'ASIN';

/** 一次 ASIN 批量检索的上限（去重后），与后端 `MAX_ASINS` 同值。 */
export const MAX_ASINS = 100;

/**
 * ASIN 批量输入 → 去重后的编号列表。
 *
 * 空格、逗号、中文逗号、分号、换行与制表符（Excel 多单元格）都是分隔符；空项丢弃，转大写后
 * 保序去重。**不校验格式**：德国站图书 ASIN 就是 ISBN-10，查不到的正确表现是零行。
 */
export function splitAsins(input: string): string[] {
  return [...new Set(input.toUpperCase().split(/[\s,，;；]+/).filter(Boolean))];
}

/**
 * 检索框当前输入不可提交的原因；可提交时为 `undefined`。
 *
 * 超上限必须拦在提交前并说清楚，不静默截断——截掉的那部分编号用户以为查过了。
 */
export function searchInputError(q: string, mode: SearchMode | undefined): string | undefined {
  if (mode !== 'ASIN') return undefined;
  const n = splitAsins(q).length;
  return n > MAX_ASINS ? `ASIN 最多 ${MAX_ASINS} 个，当前 ${n} 个` : undefined;
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
  // 两档不重叠：-150000 归「大幅上升」。「下降」「持平」不再提供快捷档（issue #125），
  // 想研究它们仍可手输区间。旧的已保存区间（如 max=-100）保留原义，只是不再命中任何档位。
  change: [
    { label: '全部', value: {} },
    { label: '上升', value: { min: -149999, max: -1 } },
    { label: '大幅上升', value: { max: -150000 } },
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
  const mode = filters.qMode ?? 'ALL_TERMS';
  const q = filters.q.trim();
  const asins = mode === 'ASIN' ? splitAsins(q) : [];
  // 关键词与 ASIN 只发所选方式那一个，切换方式后另一个不残留
  const keywordMode = mode === 'ASIN' || !q ? undefined : mode;

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
    keyword: !onlyDropped && keywordMode ? q : undefined,
    keyword_mode: !onlyDropped ? keywordMode : undefined,
    asins: !onlyDropped && asins.length ? asins : undefined,
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
    first_listing: onlyDropped ? undefined : filters.firstListing,
    // 默认开启、且只在选了首次口径时生效：品牌词对找新机会没价值，但对「全部词」榜单是
    // 额外的数据缩减，用户没要求就不该替他缩。
    exclude_brand_in_keyword:
      !onlyDropped && filters.firstListing !== undefined && filters.excludeBrandWords !== false
        ? true
        : undefined,
  };
}
