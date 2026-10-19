import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';

const { getFirstListed } = vi.hoisted(() => ({ getFirstListed: vi.fn() }));
vi.mock('@/services/analysis', () => ({ getFirstListed }));

import FirstListedSummary from './FirstListedSummary';

/**
 * 「首次入榜」数量三种呈现必须分开：有数量（含真 0）、监测起点（无法判断，不是 0）、
 * 没取到（什么都不显示）。把后两种显示成 0 会被读成「这期没有新词」。
 */
describe('FirstListedSummary', () => {
  beforeEach(() => {
    getFirstListed.mockReset();
  });
  afterEach(cleanup);

  it('显示本期数量', async () => {
    getFirstListed.mockResolvedValue({
      site: 'US', view: 'DAY', on: '2026-07-18', judgeable: true, count: 42,
      earliest_known_date: '2026-07-01',
    });
    render(<FirstListedSummary site="US" view="DAY" date="2026-07-18" />);
    expect(await screen.findByText('42')).toBeInTheDocument();
    expect(getFirstListed).toHaveBeenCalledWith('US', 'DAY', '2026-07-18');
  });

  it('真的是 0 时显示 0', async () => {
    getFirstListed.mockResolvedValue({
      site: 'US', view: 'DAY', on: '2026-07-18', judgeable: true, count: 0,
      earliest_known_date: '2026-07-01',
    });
    render(<FirstListedSummary site="US" view="DAY" date="2026-07-18" />);
    expect(await screen.findByText('0')).toBeInTheDocument();
  });

  it('早于统计起点显示「无法判断」而不是 0', async () => {
    getFirstListed.mockResolvedValue({
      site: 'US', view: 'DAY', on: '2026-07-01', judgeable: false, count: null,
      earliest_known_date: '2026-07-01',
    });
    render(<FirstListedSummary site="US" view="DAY" date="2026-07-01" />);
    expect(await screen.findByText(/该期早于首次入榜统计的起点，无法判断/)).toBeInTheDocument();
    expect(screen.queryByText('0')).toBeNull();
  });

  it('没有作用域时不发请求、不显示', () => {
    render(<FirstListedSummary site={undefined} view="DAY" date={null} />);
    expect(getFirstListed).not.toHaveBeenCalled();
    expect(screen.queryByTestId('first-listed-summary')).toBeNull();
  });

  it('取数失败时不显示数字（也不显示 0）', async () => {
    const err = vi.spyOn(console, 'error').mockImplementation(() => {});
    getFirstListed.mockImplementation(
      () =>
        new Promise((_, reject) => {
          setTimeout(() => reject(new Error('boom')), 0);
        }),
    );
    render(<FirstListedSummary site="US" view="DAY" date="2026-07-18" />);
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 30);
    });
    expect(err).toHaveBeenCalled();
    expect(screen.queryByTestId('first-listed-summary')).toBeNull();
    err.mockRestore();
  });
});
