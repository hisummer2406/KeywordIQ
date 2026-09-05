import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import GoogleTrendsButton from './GoogleTrendsButton';

describe('GoogleTrendsButton', () => {
  it('站点码缺失时不渲染任何入口', () => {
    const { container } = render(<GoogleTrendsButton keyword="power bank" />);
    expect(container).toBeEmptyDOMElement();
  });

  it('点击图标打开底部抽屉，展示查询摘要与不内嵌趋势图的说明', () => {
    render(<GoogleTrendsButton keyword="power bank" siteCode="US" />);

    // 抽屉默认关闭，内容不在文档中
    expect(screen.queryByText('Google 搜索趋势（站外需求验证）')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看 Google 搜索趋势' }));

    expect(screen.getByText('Google 搜索趋势（站外需求验证）')).toBeInTheDocument();
    expect(screen.getByText('power bank')).toBeInTheDocument();
    expect(screen.getByText(/US/)).toBeInTheDocument();
    expect(screen.getByText(/近 12 个月/)).toBeInTheDocument();
    // 免责文案必须常驻可见，不能只在跳转后才看到
    expect(screen.getByText(/不代表搜索量、销量或 Amazon 站内需求/)).toBeInTheDocument();
  });

  it('跳转按钮指向按站点码拼出的 Google Trends 页面，新标签页打开', () => {
    render(<GoogleTrendsButton keyword="wasserkocher" siteCode="DE" />);
    fireEvent.click(screen.getByRole('button', { name: '查看 Google 搜索趋势' }));

    const link = screen.getByRole('link', { name: /在 Google Trends 中打开/ });
    expect(link.getAttribute('href')).toBe(
      'https://trends.google.com/trends/explore?q=wasserkocher&geo=DE&date=today+12-m',
    );
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toContain('noopener');
  });
});
