import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { IpMeta } from '@/services/opportunityIp';
import NewTaskComposer from './NewTaskComposer';

/**
 * 发起专家任务（change adopt-antdx-opportunity-ui design D3）：产品名称必填、描述可空仍能发送；
 * 折叠区里的洛迦诺格式仍参与校验。
 */

vi.mock('@umijs/max', () => ({ request: vi.fn(), useAccess: () => ({}) }));

const api = vi.hoisted(() => ({ createResearch: vi.fn(), requestPlan: vi.fn() }));
vi.mock('@/services/opportunityIp', async (orig) => ({
  ...(await orig<typeof import('@/services/opportunityIp')>()),
  ...api,
}));
vi.mock('@/services/opportunityDossier', () => ({ linkDossierResearch: vi.fn().mockResolvedValue(undefined) }));

const meta = {
  mcp_configured: true,
  llm_configured: false,
  llm_model: null,
  markets: [{ code: 'US', name: '美国' }],
  jurisdictions: [{ code: 'US', name: '美国', note: '' }],
  recommendations: { US: ['US'] },
  query_limits: [10],
  max_queries: 5,
  max_detail_batch: 5,
  detail_tools: [],
  budget: { per_research: 20, daily_limit: 200, daily_used: 0 },
} as unknown as IpMeta;

const onCreated = vi.fn();

async function mount(preset?: React.ComponentProps<typeof NewTaskComposer>['preset']) {
  await act(async () => {
    render(<NewTaskComposer meta={meta} workspaces={[]} defaultWorkspaceId={null} preset={preset} onCreated={onCreated} />);
  });
}

const send = () => screen.getByRole('button', { name: /发给 IP 专家/ });

beforeEach(() => {
  Object.values(api).forEach((f) => f.mockReset());
  onCreated.mockReset();
  api.createResearch.mockResolvedValue({ id: 7 });
});

describe('NewTaskComposer', () => {
  it('没写产品名称不能发送，也不创建调研', async () => {
    await mount();
    await act(async () => {
      fireEvent.click(send());
    });
    expect(await screen.findByText('写一下产品名称')).toBeTruthy();
    expect(api.createResearch).not.toHaveBeenCalled();
  });

  it('描述留空、其余必填项齐全时仍可发送（Sender 空内容不提交，故走自己的按钮）', async () => {
    await mount({
      dossierId: 1,
      dossierTitle: '档案',
      product_name: '折叠杯',
      keywords: [],
      markets: ['US'],
    });
    // 法域必填：点「按市场建议」的标签加入
    fireEvent.click(screen.getByText('+ 美国'));
    await act(async () => {
      fireEvent.click(send());
    });
    expect(api.createResearch).toHaveBeenCalledTimes(1);
    expect(api.createResearch.mock.calls[0][0]).toMatchObject({ product_name: '折叠杯', description: null, jurisdictions: ['US'] });
    expect(onCreated).toHaveBeenCalledWith(7);
  });

  it('折叠区里的关键词与洛迦诺字段仍在表单里（forceRender）', async () => {
    await mount();
    expect(screen.getByText('洛迦诺分类')).toBeTruthy();
    expect(screen.getByText('关键词')).toBeTruthy();
  });
});
