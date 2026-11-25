import { describe, expect, it } from 'vitest';
import { installChromeStub } from './chrome-stub';
import type { RunState } from '../src/auto-runner';

installChromeStub();
const { describeRun } = await import('../src/auto-options');

const run = (over: Partial<RunState>): RunState => ({
  runId: 'r1',
  trigger: 'manual',
  status: 'finished',
  startedAt: '2026-11-25T00:00:00Z',
  pages: 2,
  cap: 1000,
  queue: [
    { sellerId: 'S1', site: 'US' },
    { sellerId: 'S2', site: 'US' },
    { sellerId: 'S3', site: 'US' },
  ] as RunState['queue'],
  index: 0,
  results: [],
  ...over,
});

const at = '2026-11-25T00:00:00Z';

describe('describeRun', () => {
  it('区分已处理、有效（含覆盖有限）、失败，并展示配送核对', () => {
    const text = describeRun(
      run({
        results: [
          { sellerId: 'S1', site: 'US', outcome: 'success', pages: 2, items: 30, coverage: 'limited', startedAt: at, finishedAt: at },
          { sellerId: 'S2', site: 'US', outcome: 'success', pages: 1, items: 5, coverage: 'complete', startedAt: at, finishedAt: at },
          { sellerId: 'S3', site: 'US', outcome: 'failed', reason: 'missing_page', pages: 2, items: 16, startedAt: at, finishedAt: at },
        ],
        deliverySite: 'US',
        deliveryTargets: { US: '10001' },
        deliveryText: 'New York 10001',
      }),
    );
    expect(text).toContain('已处理 3 / 3 家：有效 2 家（其中覆盖有限 1 家），失败 1 家');
    expect(text).toContain('配送核对：US 站，目标 10001，实际 New York 10001');
  });

  it('配送位置未确认时显示停止原因、停在哪家店和页面文字', () => {
    const text = describeRun(
      run({
        status: 'stopped',
        stopReason: 'delivery_unconfirmed',
        stopDetail: 'Deliver to',
        results: [
          { sellerId: 'S1', site: 'US', outcome: 'success', pages: 1, items: 5, coverage: 'complete', startedAt: at, finishedAt: at },
          { sellerId: 'S2', site: 'US', outcome: 'stopped', reason: 'delivery_unconfirmed', pages: 1, items: 0, startedAt: at, finishedAt: at },
        ],
        deliverySite: 'US',
        deliveryTargets: { US: '10001' },
        deliveryText: 'Deliver to',
      }),
    );
    expect(text).toContain('配送位置未确认');
    expect(text).toContain('停在：US 站卖家 S2（这家店未上报）');
    expect(text).toContain('页面显示的配送位置：Deliver to');
    expect(text).toContain('有效 1 家');
  });
});
