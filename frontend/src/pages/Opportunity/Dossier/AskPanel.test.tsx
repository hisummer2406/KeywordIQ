import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { DossierDetail, DossierMessage } from '@/services/opportunityDossier';
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

describe('AskPanel 问答（change refine-opportunity-chat-ui 决策 D5）', () => {
  const msg = (over: Partial<DossierMessage>): DossierMessage => ({
    id: 1,
    question: '还缺什么？',
    status: 'completed',
    error_category: null,
    answer: null,
    cited_evidence_ids: [],
    model: 'm1',
    asker_id: 1,
    asker_name: '张三',
    created_at: '2026-11-12T08:30:00',
    finished_at: null,
    ...over,
  });

  it('完成：问题与回答各一个气泡，回答渲染受限 Markdown，引用按证据类型标出', () => {
    const { container } = render(
      <AskPanel
        detail={detail({
          snapshot: { id: 9 } as DossierDetail['snapshot'],
          other_evidence: [{ id: 3 }] as DossierDetail['other_evidence'],
          messages: [
            msg({
              answer: '## 缺口\n\n- 售价\n\n[链接](javascript:alert(1))<img src=x onerror=alert(1)>',
              cited_evidence_ids: [9, 3, 4],
            }),
          ],
        })}
        onAsk={vi.fn()}
      />,
    );
    expect(screen.getByText('还缺什么？')).toBeTruthy();
    expect(screen.getByText(/张三/)).toBeTruthy();
    expect(container.querySelector('.chat-answer h2')?.textContent).toBe('缺口');
    expect(container.querySelector('.chat-answer a, .chat-answer img')).toBeNull();
    expect(screen.getByText('ABA 证据快照（当前）')).toBeTruthy();
    expect(screen.getByText('人工证据 #3')).toBeTruthy();
    expect(screen.getByText('ABA 证据快照 #4（较早）')).toBeTruthy();
    expect(screen.getByText(/模型 m1/)).toBeTruthy();
  });

  it('没有引用时说明没有标出', () => {
    render(<AskPanel detail={detail({ messages: [msg({ answer: '没有数据' })] })} onAsk={vi.fn()} />);
    expect(screen.getByText('没有标出引用的证据。')).toBeTruthy();
  });

  it('排队中显示进度', () => {
    render(<AskPanel detail={detail({ messages: [msg({ status: 'queued' })] })} onAsk={vi.fn()} />);
    expect(screen.getByText('正在根据档案证据回答…')).toBeTruthy();
  });

  it('失败与结果未知分开说，都提示可以再问', () => {
    render(
      <AskPanel
        detail={detail({
          messages: [
            msg({ id: 1, status: 'failed', error_category: 'invalid_output' }),
            msg({ id: 2, status: 'unknown', error_category: 'timeout' }),
          ],
        })}
        onAsk={vi.fn()}
      />,
    );
    expect(screen.getByText(/没有回答：模型输出不符合要求/)).toBeTruthy();
    expect(screen.getByText(/结果未知：调用超时，结果未知/)).toBeTruthy();
    expect(screen.getByText(/系统不会自动重发/)).toBeTruthy();
  });
});
