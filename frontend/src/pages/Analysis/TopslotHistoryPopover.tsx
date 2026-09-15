import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Empty, Popover, Spin, Typography } from 'antd';
import type { View } from '@/services/analysis';
import {
  getTopslotHistory,
  type TopslotHistoryItem,
  type TopslotKind,
} from '@/services/topslotHistory';
import { formatScopeDateLabel } from './scopeDateLabel';

/**
 * ASIN/品牌悬浮历史面板（issue #96）。每个非空槽位/品牌各挂一个独立实例——不是
 * 左侧列表 + 右侧详情共享一个实例的形态。「悬浮 A 与悬浮 B 是两个不同组件」这条
 * 只解决了跨对象的竞态：A、B 各自的请求序号、状态互不可见，不会互相覆盖。
 *
 * **但同一个位置的实例会被 React 复用，不会因为身份变了就重新挂载。** 表格按
 * `keyword_id` 复用行组件是 ProTable 的既有行为（同一个关键词横跨两次查询结果时，
 * React 认的是这把 key，不是这一刻具体渲染了哪个 ASIN/品牌）：切换查询日期/视图后，
 * 同一行同一槽位的 `value`/`on`/`view` 这些 props 变了，但这个 `TopslotHistoryPopover`
 * 实例本身、它内部的 `open`/`items`/`requestSeq` 状态原样留着。不主动应对的话，
 * 面板还开着时背后换了身份，上一次仍在飞的旧身份请求一旦回来，会通过序号校验把
 * 旧身份的历史数据渲染在新身份下面——见下方按身份/作用域重置的 effect。
 *
 * 悬浮触发前不发请求；打开时发首次请求，面板内滚动触底翻页；请求序号防止同一实例
 * 内旧响应覆盖新响应（组件被关闭又快速重新打开时）。总次数只在首次加载成功时写入，
 * 之后翻页返回的 total 不覆盖已显示的数字（design.md 决策 6）。
 */

const PAGE_SIZE = 20;
const PANEL_MAX_HEIGHT = 260;
const PANEL_WIDTH = 220;
/** 距底部多少像素视为"触底"，留一点余量避免边界抖动漏触发。 */
const SCROLL_THRESHOLD = 24;

type Phase = 'loading' | 'error' | 'ready';

interface Props {
  children: React.ReactNode;
  site: string;
  view: View;
  keywordId: number;
  /** 来源页面已生效查询的锚定日，必填、不做退化。 */
  on: string;
  kind: TopslotKind;
  /** ASIN 原文或品牌原文。 */
  value: string;
  /** 面板标题里展示的身份文本。 */
  label: string;
}

export default function TopslotHistoryPopover({
  children, site, view, keywordId, on, kind, value, label,
}: Props) {
  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<Phase>('loading');
  const [items, setItems] = useState<TopslotHistoryItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [page, setPage] = useState(1);
  const [hasMore, setHasMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreErrored, setMoreErrored] = useState(false);

  const requestSeq = useRef(0);
  // 写成 effect 里置位而不是 `useRef(true)` 一次性赋值：StrictMode 下 setup → cleanup →
  // setup 会跑两轮，只在 cleanup 里置 false 会让第二轮之后永远写不进去——组件其实还挂着，
  // 但每次响应都会被这道闸当成"已卸载"丢弃，面板卡在首次加载的转圈上不会再变化
  // （与 `useCachedState.ts`/`useAnalysisSession.ts` 同一条纪律，PR #97 Codex 审核）。
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  /** 清空分页与列表状态，为一次全新的首次加载做准备。不动 `phase`——调用方决定。 */
  function resetForFreshLoad() {
    setItems([]);
    setTotal(null);
    setPage(1);
    setHasMore(false);
    setLoadingMore(false);
    setMoreErrored(false);
  }

  function loadFirstPage() {
    const seq = (requestSeq.current += 1);
    setPhase('loading');
    setMoreErrored(false);
    getTopslotHistory({ site, view, keywordId, kind, value, on, page: 1, pageSize: PAGE_SIZE })
      .then((res) => {
        if (!mountedRef.current || seq !== requestSeq.current) return; // 旧请求，不覆盖新结果
        setItems(res.items);
        setTotal(res.total);
        setPage(1);
        setHasMore(res.items.length < res.total);
        setPhase('ready');
      })
      .catch(() => {
        if (!mountedRef.current || seq !== requestSeq.current) return;
        setPhase('error');
      });
  }

  function loadNextPage() {
    if (loadingMore || !hasMore || phase !== 'ready' || total === null) return;
    const nextPage = page + 1;
    const seq = (requestSeq.current += 1);
    setLoadingMore(true);
    setMoreErrored(false);
    getTopslotHistory({ site, view, keywordId, kind, value, on, page: nextPage, pageSize: PAGE_SIZE })
      .then((res) => {
        if (!mountedRef.current || seq !== requestSeq.current) return;
        const merged = [...items, ...res.items];
        setItems(merged);
        setPage(nextPage);
        // 总次数只信首次加载的那一份，不用本页返回的 total 覆盖。
        setHasMore(merged.length < total);
        setLoadingMore(false);
      })
      .catch(() => {
        if (!mountedRef.current || seq !== requestSeq.current) return;
        setMoreErrored(true);
        setLoadingMore(false);
      });
  }

  /**
   * 身份/作用域变了（`site`/`view`/`keywordId`/`kind`/`value`/`on` 任一）就让旧请求作废
   * 并清空状态——组件实例可能被 React 复用（同一行位置的槽位/品牌切了值，或来源页面
   * 换了站点/视图/日期），不是每次身份变化都会重新挂载。不这样做的表现是：面板还开着
   * 悬浮在同一个位置，背后已经换成了另一个 ASIN/品牌，之前那次仍在飞的请求一旦回来，
   * 会通过 `requestSeq` 校验（它是同一个组件实例，没人让它作废）把旧身份的历史数据
   * 渲染在新身份下面（PR #97 Codex 审核 P2）。
   *
   * 面板此刻若仍打开，直接按新身份重新拉取；关着则只清状态，下次打开走正常首次加载。
   */
  useEffect(() => {
    requestSeq.current += 1;
    resetForFreshLoad();
    if (open) {
      loadFirstPage();
    } else {
      setPhase('loading');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site, view, keywordId, kind, value, on]);

  function handleScroll(e: React.UIEvent<HTMLDivElement>) {
    const el = e.currentTarget;
    if (el.scrollTop + el.clientHeight >= el.scrollHeight - SCROLL_THRESHOLD) {
      loadNextPage();
    }
  }

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      // 每次打开都重新拉取：面板不跨次缓存，站点/视图/锚定日随来源页面变化。
      // 必须连 `loadingMore` 一起清：若上次关闭前有一次"加载更多"仍在飞，重开会让
      // `loadFirstPage` 抢先把 `requestSeq` 往前推，那次旧响应回来时通不过序号校验、
      // 连 `setLoadingMore(false)` 都执行不到——不清的话，这次新打开会带着一个永远
      // 转不停的"加载更多"转圈（PR #97 Codex 审核 P2）。
      resetForFreshLoad();
      loadFirstPage();
    }
  }

  const title = (
    <Typography.Text style={{ maxWidth: PANEL_WIDTH - 16 }} ellipsis={{ tooltip: label }}>
      {label}
      {total !== null && (
        <Typography.Text type="secondary" style={{ marginInlineStart: 6, fontWeight: 'normal' }}>
          · ABA 上榜次数（{total}）
        </Typography.Text>
      )}
    </Typography.Text>
  );

  let body: React.ReactNode;
  if (phase === 'loading') {
    body = (
      <div style={{ display: 'flex', justifyContent: 'center', padding: '20px 0' }}>
        <Spin size="small" />
      </div>
    );
  } else if (phase === 'error') {
    body = (
      <Alert
        type="error"
        showIcon
        message="加载失败"
        action={
          <Button size="small" onClick={loadFirstPage}>
            重试
          </Button>
        }
      />
    );
  } else if (total === 0) {
    body = <Empty description="没有匹配历史" image={Empty.PRESENTED_IMAGE_SIMPLE} />;
  } else {
    body = (
      <div
        data-testid="topslot-history-scroll"
        onScroll={handleScroll}
        style={{ maxHeight: PANEL_MAX_HEIGHT, overflowY: 'auto' }}
      >
        {items.map((item, i) => (
          <div
            key={`${item.date}-${i}`}
            style={{
              display: 'flex', justifyContent: 'space-between', gap: 12,
              padding: '4px 0', fontSize: 12,
            }}
          >
            <span>{formatScopeDateLabel(item.date, view)}</span>
            <span style={{ color: '#1677ff', flexShrink: 0 }}>第 {item.rank} 名</span>
          </div>
        ))}
        {loadingMore && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '8px 0' }}>
            <Spin size="small" />
          </div>
        )}
        {moreErrored && (
          <div style={{ textAlign: 'center', padding: '8px 0' }}>
            <Button size="small" danger onClick={loadNextPage}>
              加载更多失败，点击重试
            </Button>
          </div>
        )}
        {!hasMore && !loadingMore && !moreErrored && (
          <div style={{ textAlign: 'center', color: '#bfbfbf', fontSize: 12, padding: '6px 0' }}>
            已展示全部记录
          </div>
        )}
      </div>
    );
  }

  return (
    <Popover
      open={open}
      onOpenChange={handleOpenChange}
      trigger="hover"
      mouseEnterDelay={0.2}
      title={title}
      content={<div style={{ width: PANEL_WIDTH }}>{body}</div>}
      // Popover 的浮层由 rc-trigger 挂载到独立容器，与触发元素（如商品外链 <a>）不共享
      // DOM 树，面板内的滚动与重试点击不会冒泡到外层链接，不会造成误跳转。
    >
      {children}
    </Popover>
  );
}
