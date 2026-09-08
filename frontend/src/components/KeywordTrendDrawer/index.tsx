import { Alert, Drawer, Radio, Space, Spin, Typography } from 'antd';
import { useEffect, useRef, useState } from 'react';
import AddToLibraryModal from '@/pages/Analysis/AddToLibraryModal';
import { formatScopeDateLabel } from '@/pages/Analysis/scopeDateLabel';
import type { View } from '@/services/analysis';
import { getKeywordTrend, type KeywordTrendResponse, type TrendRange } from '@/services/keywordTrend';
import TrendTimelineChart, { STATUS_META } from './TrendTimelineChart';

const { Text } = Typography;

/**
 * 关键词排名趋势详情：市场分析与我的词库共用同一个抽屉组件（issue #86）。
 *
 * **来源锚点固定不变**：`on` 取打开那一刻所在页面正在看的期次（不是"今天"），此后切换
 * 视图/范围时原样重发同一个 `on`——后端按"取 <= 该日期的最新一期"映射到目标视图
 * （见 keyword_trend_service 的 design 决策 4）。这里不在前端重新计算映射，避免与后端
 * 各算一遍、漂移后互相矛盾。
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

const RANGE_OPTIONS: { label: string; value: TrendRange }[] = [
  { label: '近 30 天', value: 'RECENT_30D' },
  { label: '全部可用历史', value: 'ALL' },
];

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

export default function KeywordTrendDrawer({
  open, onClose, keywordId, keyword, site, view, on, hideAddToLibrary, canAddToLibrary = true,
}: Props) {
  const showAddToLibrary = canAddToLibrary && !hideAddToLibrary;
  const [viewState, setViewState] = useState<View>(view);
  const [range, setRange] = useState<TrendRange>('RECENT_30D');
  const [data, setData] = useState<KeywordTrendResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [errored, setErrored] = useState(false);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const requestSeq = useRef(0);

  // 每次打开重置到来源视图与默认范围——不保留上一个关键词遗留的切换状态。
  useEffect(() => {
    if (open) {
      setViewState(view);
      setRange('RECENT_30D');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, keywordId]);

  useEffect(() => {
    if (!open) return;
    const seq = (requestSeq.current += 1);
    setLoading(true);
    setErrored(false);
    getKeywordTrend({ keywordId, site, view: viewState, range, on })
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
  }, [open, keywordId, site, viewState, range, on]);

  return (
    <Drawer
      title={`关键词 ABA 排名趋势 · ${keyword}`}
      placement="bottom"
      height={480}
      open={open}
      onClose={onClose}
      destroyOnClose
    >
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Space size={24} wrap>
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={VIEW_OPTIONS}
            value={viewState}
            onChange={(e) => setViewState(e.target.value as View)}
          />
          <Radio.Group
            optionType="button"
            buttonStyle="solid"
            options={RANGE_OPTIONS}
            value={range}
            onChange={(e) => setRange(e.target.value as TrendRange)}
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

        {/* `data !== null` 排除首次挂载、请求还没回来的那一瞬间——那时不该说"暂无数据"，
            只是"还没查完"，两者含义不同。 */}
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
            <Space size={32} wrap style={{ fontSize: 13 }}>
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
            </Space>

            <Spin spinning={loading}>
              <TrendTimelineChart points={data.points} view={data.view} />
            </Spin>

            <Space size={16} wrap style={{ fontSize: 12, color: '#8c8c8c' }}>
              {(Object.keys(STATUS_META) as (keyof typeof STATUS_META)[]).map((s) => (
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
          </>
        )}
      </Space>

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
