import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { DossierDetail as Detail, DossierMessage, ManualEvidence } from '@/services/opportunityDossier';
import AskPanel from './AskPanel';
import DossierDetail from './DossierDetail';

// services 引入 @umijs/max 的 request；不 mock 会把 Umi 打包器拉进 jsdom
vi.mock('@umijs/max', () => ({ request: vi.fn(), useAccess: () => ({}) }));
const getDossier = vi.fn();
vi.mock('@/services/opportunityDossier', async (orig) => ({
  ...(await orig<typeof import('@/services/opportunityDossier')>()),
  getDossier: (...args: unknown[]) => getDossier(...args),
}));

function manual(id: number): ManualEvidence {
  return {
    id,
    category: 'sales',
    content: '月销约 3000',
    source_name: '卖家精灵网页',
    source_url: null,
    observed_date: '2026-11-12',
    created_by: 1,
    created_by_name: 'a',
    created_at: '2026-11-12T00:00:00Z',
    voided_at: null,
    voided_by_name: null,
  };
}

function message(cited: number[]): DossierMessage {
  return {
    id: 1,
    question: '头部稳吗',
    status: 'completed',
    error_category: null,
    answer: '回答',
    cited_evidence_ids: cited,
    model: 'm',
    asker_id: 1,
    asker_name: 'a',
    created_at: '2026-11-12T00:00:00Z',
    finished_at: '2026-11-12T00:00:00Z',
  };
}

function detail(over: Partial<Detail> = {}): Detail {
  return {
    dossier: {
      id: 1,
      title: '档案',
      idea: null,
      site: 'US',
      site_name: '美国',
      site_domain: 'amazon.com',
      status: 'researching',
      recheck_date: null,
      archived_at: null,
      creator_id: 1,
      creator_name: 'a',
      assignee_id: 1,
      assignee_name: 'a',
      created_at: '2026-11-12T00:00:00Z',
      updated_at: '2026-11-12T00:00:00Z',
    },
    today: '2026-11-13',
    keywords: [],
    snapshot: null,
    snapshot_count: 0,
    snapshot_stale: null,
    latest_anchor_day: null,
    latest_anchor_week: null,
    changes: null,
    gaps: [],
    other_evidence: [],
    decisions: [],
    ip: { state: 'unchecked', researches: [] },
    pricing: { state: 'none', sheets: [] },
    messages: [],
    asking: false,
    llm_configured: true,
    ...over,
  };
}

describe('追问引用的证据类型（PR #187 复审）', () => {
  it('旧快照仍标成 ABA 快照，人工证据按 id 识别', () => {
    const d = detail({
      snapshot: { id: 9, anchor_day: null, anchor_week: null, created_by: 1, created_by_name: 'a', created_at: '', payload: {} as never },
      gaps: [{ category: 'sales', connected: false, evidence: [manual(5)] }],
      messages: [message([3, 5, 9])],
    });
    render(<AskPanel detail={d} onAsk={async () => undefined} />);
    expect(screen.getByText('ABA 证据快照 #3（较早）')).toBeTruthy();
    expect(screen.getByText('人工证据 #5')).toBeTruthy();
    expect(screen.getByText('ABA 证据快照（当前）')).toBeTruthy();
    expect(screen.queryByText('人工证据 #3')).toBeNull();
  });
});

describe('详情加载失败（PR #187 复审）', () => {
  const props = {
    id: 1,
    currentUserId: 1,
    onBack: () => undefined,
    onChanged: () => undefined,
    onStartResearch: () => undefined,
    onOpenResearch: () => undefined,
    onOpenPricingSheet: () => undefined,
  };
  beforeEach(() => getDossier.mockReset());

  it('网络错误不显示「档案不存在」', async () => {
    getDossier.mockRejectedValueOnce(new Error('network'));
    await act(async () => {
      render(<DossierDetail {...props} />);
    });
    expect(screen.queryByText('档案不存在')).toBeNull();
  });

  it('只有 404 才显示「档案不存在」', async () => {
    getDossier.mockRejectedValueOnce({ response: { status: 404 } });
    await act(async () => {
      render(<DossierDetail {...props} />);
    });
    expect(screen.getByText('档案不存在')).toBeTruthy();
  });
});
