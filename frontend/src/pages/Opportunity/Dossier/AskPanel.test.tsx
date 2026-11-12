import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DossierDetail } from '@/services/opportunityDossier';
import AskPanel from './AskPanel';

/** 档案追问（change adopt-antdx-opportunity-ui design D3）：建议问题点击即发，回车发送，忙时不可发。 */

vi.mock('@umijs/max', () => ({ request: vi.fn(), useAccess: () => ({}) }));

const detail = (over: Partial<DossierDetail> = {}) =>
  ({
    snapshot: null,
    gaps: [],
    other_evidence: [],
    messages: [],
    asking: false,
    llm_configured: true,
    ...over,
  }) as unknown as DossierDetail;

const box = () => screen.getByRole('textbox') as HTMLTextAreaElement;

describe('AskPanel 输入', () => {
  it('回车发送输入的问题', async () => {
    const onAsk = vi.fn().mockResolvedValue(undefined);
    render(<AskPanel detail={detail()} onAsk={onAsk} />);
    fireEvent.change(box(), { target: { value: '还缺什么证据？' } });
    await act(async () => {
      fireEvent.keyDown(box(), { key: 'Enter', code: 'Enter' });
    });
    expect(onAsk).toHaveBeenCalledWith('还缺什么证据？');
  });

  it('没有历史问答时显示建议问题，点击即发送该问题', async () => {
    const onAsk = vi.fn().mockResolvedValue(undefined);
    render(<AskPanel detail={detail()} onAsk={onAsk} />);
    await act(async () => {
      fireEvent.click(screen.getByText('这几个词的头部商品稳定吗？'));
    });
    expect(onAsk).toHaveBeenCalledWith('这几个词的头部商品稳定吗？');
  });

  it('上一个问题还在回答时输入框不可用，不发送', async () => {
    const onAsk = vi.fn();
    render(<AskPanel detail={detail({ asking: true })} onAsk={onAsk} />);
    expect(box().disabled).toBe(true);
    expect(box().placeholder).toContain('还在回答中');
    expect(onAsk).not.toHaveBeenCalled();
  });

  it('大模型未配置时不显示输入框', () => {
    render(<AskPanel detail={detail({ llm_configured: false })} onAsk={vi.fn()} />);
    expect(screen.queryByRole('textbox')).toBeNull();
    expect(screen.getByText(/大模型未配置/)).toBeTruthy();
  });
});
