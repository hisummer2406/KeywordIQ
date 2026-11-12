import { CloseOutlined, PlusOutlined } from '@ant-design/icons';
import { Button, Input, Modal, Popconfirm, Tag, Tooltip, Typography, theme } from 'antd';
import { useState } from 'react';
import ShareBar from '@/components/ShareBar';
import { amazonProductUrl } from '@/pages/Analysis/amazonProductUrl';
import type { DossierDetail, KeywordEvidence } from '@/services/opportunityDossier';
import { formatRank, parseKeywordInput, rankDelta } from './dossierView';
import RankStrip from './RankStrip';

/**
 * 关键词与 ABA 证据。只摆事实：在榜覆盖、排名、点击前三商品与份额。
 * 不给评分，也不给「值得做」一类结论；在榜期数不足时明说不判断趋势。
 */

const FIRST_BASIS: Record<string, string> = {
  IMPORTED: '确认首次',
  EARLIEST_OBSERVED: '最早可观测（此前可能已出现）',
  UNKNOWN: '未知',
  ABSENT: '未上过该榜',
};

interface Props {
  detail: DossierDetail;
  onAdd: (keywords: string[]) => Promise<void>;
  onRemove: (keywordRowId: number) => Promise<void>;
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  const { token } = theme.useToken();
  return (
    <div style={{ minWidth: 96 }}>
      <div style={{ fontSize: 12, color: token.colorTextTertiary }}>{label}</div>
      <div style={{ fontVariantNumeric: 'tabular-nums' }}>{children}</div>
    </div>
  );
}

function KeywordCard({ kw, domain, onRemove }: { kw: KeywordEvidence; domain: string | null; onRemove?: () => Promise<void> }) {
  const { token } = theme.useToken();
  const day = kw.day;
  const delta = day ? rankDelta(day.rank_7d_ago?.ranking, day.latest?.ranking) : null;
  const monitor = kw.first_evidence?.monitor;
  const weekRanked = (kw.week?.points ?? []).filter((p) => p[1] === 'R');
  return (
    <div style={{ border: `1px solid ${token.colorBorderSecondary}`, borderRadius: 10, padding: 14, background: token.colorBgContainer }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
        <Typography.Text strong copyable style={{ fontSize: 15 }}>
          {kw.keyword}
        </Typography.Text>
        {!kw.resolved && <Tag>保留期内未进入过该站点 ABA 榜单</Tag>}
        {kw.resolved && kw.thin && (
          <Tooltip title="日榜在榜不足 7 期，排名的起伏还说明不了趋势">
            <Tag color="warning">在榜期数不足，不判断趋势</Tag>
          </Tooltip>
        )}
        <span style={{ flex: 1 }} />
        {onRemove && (
          <Popconfirm title="从档案移除这个词？" description="已有的证据快照不受影响。" onConfirm={onRemove}>
            <Button type="text" size="small" icon={<CloseOutlined />} aria-label="移除关键词" />
          </Popconfirm>
        )}
      </div>

      {!kw.resolved || !day ? (
        <Typography.Text type="secondary">
          这个词在保留期内没有进过榜单，所以没有排名和商品数据。这不等于没有需求——只表示搜索频率排名没进入报表。之后进榜了，刷新证据即可看到。
        </Typography.Text>
      ) : (
        <>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 20, alignItems: 'flex-end' }}>
            <Fact label="最新在榜排名">
              {day.latest ? (
                <>
                  <span style={{ fontSize: 18, fontWeight: 600 }}>{formatRank(day.latest.ranking)}</span>
                  <span style={{ color: token.colorTextTertiary, marginLeft: 6 }}>{day.latest.date}</span>
                </>
              ) : (
                '没有在榜记录'
              )}
            </Fact>
            <Fact label="7 天前同日">
              {day.rank_7d_ago ? (
                <>
                  {formatRank(day.rank_7d_ago.ranking)}
                  {delta && delta.direction !== 'flat' && (
                    <span style={{ marginLeft: 6, color: delta.direction === 'up' ? token.colorSuccess : token.colorError }}>
                      {delta.direction === 'up' ? '↑' : '↓'} {formatRank(delta.places)}
                    </span>
                  )}
                </>
              ) : (
                <Tooltip title="7 天前那一天不在榜，不拿邻近日期代替">
                  <span style={{ color: token.colorTextTertiary }}>当日不在榜</span>
                </Tooltip>
              )}
            </Fact>
            <Fact label="日榜在榜 / 已导入">
              {day.ranked_periods} / {day.imported_periods} 期
            </Fact>
            <Fact label="最佳排名">{day.best ? `${formatRank(day.best.ranking)}（${day.best.date}）` : '—'}</Fact>
            <Fact label="首次进入监测">
              {monitor?.date ? (
                <Tooltip title={FIRST_BASIS[monitor.basis] ?? monitor.basis}>
                  <span>
                    {monitor.date}
                    {monitor.basis !== 'IMPORTED' && <span style={{ color: token.colorTextTertiary }}> *</span>}
                  </span>
                </Tooltip>
              ) : (
                '—'
              )}
            </Fact>
          </div>

          <div style={{ marginTop: 10 }}>
            <RankStrip points={day.points} />
            <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
              日榜逐期（{day.points[0]?.[0]} 至 {day.points[day.points.length - 1]?.[0]}）：圆点是在榜，底部灰线是该期没进榜，空格是报表未导入。悬停看每一期。
            </div>
          </div>

          {weekRanked.length > 0 && (
            <div style={{ marginTop: 8, fontSize: 12 }}>
              <span style={{ color: token.colorTextTertiary }}>周榜在榜 {weekRanked.length} / {kw.week?.imported_periods ?? 0} 期：</span>
              {weekRanked.slice(-9).map((p) => (
                <Tooltip key={p[0]} title={p[0]}>
                  <Tag style={{ marginInlineEnd: 4, fontVariantNumeric: 'tabular-nums' }}>{formatRank(p[2])}</Tag>
                </Tooltip>
              ))}
            </div>
          )}

          {kw.top3 && kw.top3.products.length > 0 && (
            <div style={{ marginTop: 12 }}>
              <div style={{ fontSize: 12, color: token.colorTextTertiary, marginBottom: 4 }}>
                {kw.top3.date} 点击前三商品（合计点击份额 {kw.top3.click_share_sum ?? '—'}%）
                {kw.top1_churn && kw.top1_churn.ranked_periods > 0 && (
                  <>
                    {' · '}近 30 天在榜 {kw.top1_churn.ranked_periods} 期里，点击第一换过{' '}
                    <b style={{ color: token.colorText }}>{kw.top1_churn.distinct_top1}</b> 个商品
                  </>
                )}
              </div>
              {kw.top3.products.map((p) => {
                const href = amazonProductUrl(p.asin, domain ?? undefined);
                return (
                  <div key={p.slot} style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '3px 0' }}>
                    <Tag style={{ marginInlineEnd: 0 }}>#{p.slot}</Tag>
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer" style={{ width: 104, flex: 'none' }}>
                        {p.asin}
                      </a>
                    ) : (
                      <span style={{ width: 104, flex: 'none' }}>{p.asin}</span>
                    )}
                    <Typography.Text ellipsis={{ tooltip: p.title }} style={{ flex: 1, minWidth: 0 }}>
                      {p.title || <span style={{ color: token.colorTextQuaternary }}>报表未给标题</span>}
                    </Typography.Text>
                    <Tooltip title="点击份额">
                      <div style={{ width: 84, flex: 'none' }}>
                        <ShareBar value={p.click_share ?? 0} />
                      </div>
                    </Tooltip>
                    <Tooltip title="转化份额">
                      <div style={{ width: 84, flex: 'none' }}>
                        <ShareBar value={p.conversion_share ?? 0} />
                      </div>
                    </Tooltip>
                  </div>
                );
              })}
              {(kw.top3.brands.length > 0 || kw.top3.categories.length > 0) && (
                <div style={{ marginTop: 6, fontSize: 12, color: token.colorTextSecondary }}>
                  <Tooltip title="这是这个词下点击最多的前三个品牌与类目，是关键词级的排行，不与上面三个商品一一对应">
                    <span>
                      {kw.top3.brands.length > 0 && <>该词点击前三品牌：{kw.top3.brands.join('、')}　</>}
                      {kw.top3.categories.length > 0 && <>前三类目：{kw.top3.categories.join('、')}</>}
                    </span>
                  </Tooltip>
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export default function KeywordEvidencePanel({ detail, onAdd, onRemove }: Props) {
  const { token } = theme.useToken();
  const [adding, setAdding] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const payload = detail.snapshot?.payload;
  const rowId = new Map(detail.keywords.map((k) => [k.keyword, k.id]));
  const canRemove = detail.keywords.length > 1;
  const pending = parseKeywordInput(adding ?? '');
  const room = 20 - detail.keywords.length;

  const save = async () => {
    if (!pending.length) return;
    setSaving(true);
    try {
      await onAdd(pending);
      setAdding(null);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 8 }}>
        <Typography.Title level={5} style={{ margin: 0, flex: 1 }}>
          关键词与 ABA 证据
        </Typography.Title>
        <Button size="small" icon={<PlusOutlined />} disabled={room <= 0} onClick={() => setAdding('')}>
          加关键词
        </Button>
      </div>
      {payload ? (
        <>
          <Typography.Paragraph type="secondary" style={{ fontSize: 12, marginBottom: 8 }}>
            来源：Amazon ABA 热门搜索词报表（{payload.site}）。日榜最新期 {payload.anchor_day ?? '尚未导入'}，周榜最新期 {payload.anchor_week ?? '尚未导入'}。排名是搜索频率排名，数字越小越靠前；不在榜不等于没有需求。
          </Typography.Paragraph>
          {payload.shared_asins.length > 0 && (
            <div style={{ marginBottom: 10, padding: '8px 12px', borderRadius: 8, background: token.colorFillQuaternary }}>
              <Typography.Text strong>跨词共同商品</Typography.Text>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                （出现在两个及以上关键词的点击前三里）
              </Typography.Text>
              {payload.shared_asins.map((s) => {
                const href = amazonProductUrl(s.asin, detail.dossier.site_domain ?? undefined);
                return (
                  <div key={s.asin} style={{ display: 'flex', gap: 10, alignItems: 'baseline', marginTop: 4 }}>
                    {href ? (
                      <a href={href} target="_blank" rel="noreferrer">
                        {s.asin}
                      </a>
                    ) : (
                      <span>{s.asin}</span>
                    )}
                    <Typography.Text ellipsis={{ tooltip: s.title }} style={{ flex: 1, minWidth: 0 }}>
                      {s.title}
                    </Typography.Text>
                    <Tooltip title={s.keywords.join('、')}>
                      <Tag>{s.keywords.length} 个词</Tag>
                    </Tooltip>
                  </div>
                );
              })}
            </div>
          )}
          <div style={{ display: 'grid', gap: 10 }}>
            {payload.keywords.map((kw) => {
              const id = rowId.get(kw.keyword);
              return (
                <KeywordCard
                  key={kw.keyword}
                  kw={kw}
                  domain={detail.dossier.site_domain}
                  onRemove={canRemove && id !== undefined ? () => onRemove(id) : undefined}
                />
              );
            })}
          </div>
        </>
      ) : (
        <Typography.Text type="secondary">还没有证据快照，点右上角「刷新证据」生成。</Typography.Text>
      )}

      <Modal
        open={adding !== null}
        title="加关键词"
        okText={`加入 ${pending.length} 个并刷新证据`}
        okButtonProps={{ disabled: !pending.length || pending.length > room, loading: saving }}
        onOk={() => save().catch(() => undefined)}
        onCancel={() => setAdding(null)}
        destroyOnClose
      >
        <Input.TextArea
          autoFocus
          autoSize={{ minRows: 4, maxRows: 10 }}
          placeholder="每行一个关键词"
          value={adding ?? ''}
          onChange={(e) => setAdding(e.target.value)}
        />
        <Typography.Text type={pending.length > room ? 'danger' : 'secondary'} style={{ fontSize: 12 }}>
          一个档案最多 20 个词，还能加 {Math.max(room, 0)} 个。
        </Typography.Text>
      </Modal>
    </div>
  );
}
