import { Alert, Drawer, Radio, Space, Spin, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import AddToLibraryModal from '@/pages/Analysis/AddToLibraryModal';
import { formatScopeDateLabel } from '@/pages/Analysis/scopeDateLabel';
import type { View } from '@/services/analysis';
import { type FirstEvidence, getKeywordTrend, type KeywordTrendResponse } from '@/services/keywordTrend';
import TrendTimelineChart, { COMMON_STATUSES, STATUS_META } from './TrendTimelineChart';

const { Text } = Typography;

/**
 * 关键词排名趋势详情：市场分析与我的词库共用同一个抽屉组件（issue #86）。
 *
 * **来源锚点固定不变**：`on` 取打开那一刻所在页面正在看的期次（不是"今天"），此后切换
 * 视图时原样重发同一个 `on`——后端按"取 <= 该日期的最新一期"映射到目标视图
 * （见 keyword_trend_service 的 design 决策 4）。这里不在前端重新计算映射，避免与后端
 * 各算一遍、漂移后互相矛盾。
 *
 * **范围恒为「全部可用历史」，不提供切换**（issue #94）：曾经有过「近 30 天 / 全部可用
 * 历史」的切换，用户反馈日常只用得到全部历史，30 天窗口反而是多余的一步选择，故直接
 * 去掉切换、固定传 `range: 'ALL'`。后端接口仍接受 `RECENT_30D`，只是前端不再暴露入口。
 *
 * **旧请求不覆盖新结果**：用递增的请求序号而非 AbortController——umi 的 `request` 没有
 * 现成的取消口子，序号比较更简单也更不容易漏判。
 */

interface Props {
  open: boolean;
  onClose: () => void;
  keywordId: number;
  keyword: string;
  site: string;
  /** 打开详情时来源页面所在的视图。 */
  view: View;
  /** 来源锚定期（该站点该视图当前正在展示的期次），不传则由后端解析为最新一期。 */
  on?: string;
  /**
   * 从我的词库打开时传 true：该词已在当前分组，详情内不重复提供「加入词库」入口
   * ——对同一件事出现两个语义重复的按钮。
   */
  hideAddToLibrary?: boolean;
  /**
   * 调用方按 `access.canViewKeyword` 传入，默认 true。
   *
   * 「加入词库」调的是词库域的接口（`keyword:view`），与这个抽屉本身要求的
   * `analysis:view`/`keyword:view` 任一即可不是同一个权限点——市场分析入口只
   * 持有 `analysis:view` 的用户能打开抽屉，却不该看到一个点了必定 403 的按钮
   * （PR #87 审核 P2）。我的词库入口本就要求 `keyword:view` 才能进页面，
   * 不必单独传。
   */
  canAddToLibrary?: boolean;
}

const VIEW_OPTIONS: { label: string; value: View }[] = [
  { label: '按天', value: 'DAY' },
  { label: '按周', value: 'WEEK' },
];

function StatLine({ label, stat, view }: { label: string; stat: { date: string; ranking: number } | null; view: View }) {
  return (
    <div>
      <Text type="secondary">{label}：</Text>
      {stat ? (
        <Text strong>
          {stat.ranking.toLocaleString()}
          <Text type="secondary" style={{ marginInlineStart: 6, fontWeight: 'normal' }}>
            （{formatScopeDateLabel(stat.date, view)}）
          </Text>
        </Text>
      ) : (
        <Text type="secondary">无数据</Text>
      )}
    </div>
  );
}

/**
 * 一项首次证据的可读文案（keyword-trend-detail「详情展示三项首次证据」）。「最早可观测」必须说明
 * 此前可能已出现——它只是库内最早能看到的日期，显示成确认首次就是把「不知道」说成「确定是新的」。
 */
export function describeFirstEvidence(ev: FirstEvidence, board: string): string {
  const d = ev.date ?? '';
  const via = ev.period_type ? `（${ev.period_type === 'WEEK' ? '周报' : '日报'}）` : '';
  switch (ev.basis) {
    case 'IMPORTED':
      return `${d}${via}`;
    case 'EARLIEST_OBSERVED':
      return `最早可观测 ${d}${via}（此前可能已出现）`;
    case 'ABSENT':
      return `未上过${board}`;
    default:
      return '未知';
  }
}

function FirstEvidenceLines({ evidence }: { evidence: NonNullable<KeywordTrendResponse['first_evidence']> }) {
  return (
    <>
      <div>
        <Text type="secondary">首次进入日榜：</Text>
        <Text>{describeFirstEvidence(evidence.day, '日榜')}</Text>
      </div>
      <div>
        <Text type="secondary">首次进入周榜：</Text>
        <Text>{describeFirstEvidence(evidence.week, '周榜')}</Text>
      </div>
      <div>
        <Text type="secondary">首次进入监测：</Text>
        <Text>{describeFirstEvidence(evidence.monitor, '榜')}</Text>
      </div>
    </>
  );
}

export default function KeywordTrendDrawer({
  open, onClose, keywordId, keyword, site, view, on, hideAddToLibrary, canAddToLibrary = true,
}: Props) {
  const showAddToLibrary = canAddToLibrary && !hideAddToLibrary;
  const [viewState, setViewState] = useState<View>(view);
  const [data, setData] = useState<KeywordTrendResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [errored, setErrored] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const requestSeq = useRef(0);

  // 每次打开（含关闭后重开、切换到另一个关键词）都重置到来源视图——不保留上一个关键词
  // 遗留的视图切换状态。范围恒为「全部可用历史」（issue #94），不提供切换，无需重置。
  useEffect(() => {
    if (open) {
      setViewState(view);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, keywordId]);

  useEffect(() => {
    if (!open) return;
    const seq = (requestSeq.current += 1);
    setLoading(true);
    setErrored(false);
    getKeywordTrend({ keywordId, site, view: viewState, range: 'ALL', on })
      .then((res) => {
        if (seq !== requestSeq.current) return; // 旧请求，不覆盖新结果
        setData(res);
      })
      .catch(() => {
        if (seq !== requestSeq.current) return;
        setErrored(true); // 加载失败不能显示成"无数据"，两者含义完全不同
      })
      .finally(() => {
        if (seq === requestSeq.current) setLoading(false);
      });
  }, [open, keywordId, site, viewState, on]);

  return (
    <Drawer
      title={`关键词 ABA 排名趋势 · ${keyword}`}
      placement="bottom"
      height={480}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      {/* 抽屉铺满视口宽度（placement="bottom"），但内容没有必要跟着铺满——不加这层居中
          容器，内容会贴在视口左侧，右边留一大片空白，看起来像没对齐（用户反馈）。
          `width: fit-content` 让容器宽度跟着实际内容（文字列 + 760px 图表）走，
          不用猜一个固定像素值，两栏布局改动时也不必跟着改这个数字。 */}
      <div style={{ width: 'fit-content', maxWidth: '100%', margin: '0 auto' }}>
        {/* 文字统计在左（竖排），日/周切换 + 图表 + 图例在右——切换控件紧贴在图表正上方，
            不放在整个内容区顶部：用户反馈原先放在最上面时，图表看起来像是另起一段、跟
            控件对不上。控件不放进下面的 `data !== null` 条件里，是为了在加载中/失败/
            无数据这几种状态下也能切视图重试，不因为还没数据就把控件一起藏起来。
            `flexWrap: 'wrap'`：左列 `flexShrink: 0` 且右列内嵌一个固定 760px 宽的
            `<svg>`，两者所需宽度是相加而非取大——视口窄于「文字列宽度 + 32 + 760」时
            没有收缩空间，会被迫横向溢出、右侧图表被裁掉一截（Codex 审核 PR #95 第三轮
            P2）。允许换行后窄视口下改为上下堆叠，不再横向溢出；正常宽度下两列仍并排，
            这行改动不影响当前的默认视觉。 */}
        <div
          data-testid="trend-drawer-columns"
          style={{
            display: 'flex', gap: 32, alignItems: 'flex-start', flexWrap: 'wrap',
          }}
        >
          {/* `data !== null` 排除首次挂载、请求还没回来的那一瞬间——那时不该说"暂无数据"，
              两者含义不同；这一列在没有数据前干脆不渲染，不留一个空的占位列。 */}
          {!errored && data !== null && data.on !== null && (
            <Space direction="vertical" size={12} style={{ fontSize: 13, flexShrink: 0 }}>
              <div>
                <Text type="secondary">实际使用的锚定期：</Text>
                <Text strong>{formatScopeDateLabel(data.on, data.view)}</Text>
              </div>
              <div>
                <Text type="secondary">实际数据覆盖范围：</Text>
                {data.coverage ? (
                  <Text>
                    {formatScopeDateLabel(data.coverage.start, data.view)} ～{' '}
                    {formatScopeDateLabel(data.coverage.end, data.view)}
                  </Text>
                ) : (
                  <Text type="secondary">无数据</Text>
                )}
              </div>
              <StatLine label="区间最好排名" stat={data.best_ranking} view={data.view} />
              <StatLine label="最近可用排名" stat={data.latest_available} view={data.view} />
              {data.first_evidence ? <FirstEvidenceLines evidence={data.first_evidence} /> : null}
            </Space>
          )}

          <div>
            <Space size={24} wrap style={{ marginBottom: 12 }}>
              <Radio.Group
                optionType="button"
                buttonStyle="solid"
                options={VIEW_OPTIONS}
                value={viewState}
                onChange={(e) => setViewState(e.target.value as View)}
              />
              {showAddToLibrary && (
                <a onClick={() => setLibraryOpen(true)}>加入词库</a>
              )}
            </Space>

            {loading && data === null && (
              <div style={{ textAlign: 'center', padding: 40 }}>
                <Spin />
              </div>
            )}

            {errored && (
              <Alert type="error" showIcon message="加载失败，请重试" />
            )}

            {!errored && !loading && data !== null && data.on === null && (
              <Alert type="info" showIcon message={`该站点 ${data.view === 'DAY' ? '天' : '周'}视图暂无任何数据`} />
            )}

            {/* 下面这段一律读 `data.view`（响应自带的视图），不读 `viewState`（用户当前选中
                的视图）：切换 DAY/WEEK 时 `viewState` 立即变、`data` 要等新响应回来才换，
                中间这段窗口里 `loading` 只包住小图（见下方 Spin），若统计文字与日期格式化
                仍按 `viewState` 走，会把上一次响应（比如 DAY）的日期当新视图（WEEK）格式化
                ——数字没变但含义被读反了（PR #87 复审 P2）。 */}
            {!errored && data !== null && data.on !== null && (
              <>
                <Spin spinning={loading}>
                  <TrendTimelineChart points={data.points} view={data.view} />
                </Spin>

                {/* 图例只常驻三种常见状态（用户反馈），另外两种少见情况（该期报表已导入但未
                    收录/已超出保留范围）不占常驻位置——但判定口径不变，真出现时图上的点与
                    悬浮提示仍按各自真实状态显示，见 COMMON_STATUSES 的注释。居中对齐图表
                    ——默认左对齐时图例贴在图表左侧，跟图表的视觉中心对不上（用户反馈）。 */}
                <div style={{ display: 'flex', justifyContent: 'center', marginTop: 12 }}>
                  <Space size={16} wrap style={{ fontSize: 12, color: '#8c8c8c' }}>
                    {COMMON_STATUSES.map((s) => (
                      <Space key={s} size={4}>
                        <span
                          style={{
                            display: 'inline-block', width: 8, height: 8, borderRadius: '50%',
                            background: STATUS_META[s].color,
                          }}
                        />
                        {STATUS_META[s].label}
                      </Space>
                    ))}
                  </Space>
                </div>
              </>
            )}
          </div>
        </div>
      </div>

      {showAddToLibrary && (
        <AddToLibraryModal
          open={libraryOpen}
          site={site}
          keywordIds={[keywordId]}
          onClose={() => setLibraryOpen(false)}
          onAdded={() => undefined}
        />
      )}
    </Drawer>
  );
}
