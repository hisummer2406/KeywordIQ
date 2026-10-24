import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { formatSiteLabel, SiteFlag, siteFlagSrc, SiteLabel, toSiteSelectOptions } from './siteLabel';

const US = { code: 'US', name_zh: '美国', domain: 'amazon.com' };

describe('siteFlagSrc', () => {
  it.each(['US', 'GB', 'DE', ' us '])('%s 有随包的 SVG 国旗', (code) => {
    expect(siteFlagSrc(code)).toMatch(/\.svg|^data:image\/svg\+xml/);
  });

  it.each(['', 'FR', 'USA'])('未登记的站点 %s 不出国旗', (code) => {
    expect(siteFlagSrc(code)).toBeUndefined();
  });
});

describe('SiteFlag', () => {
  it('是对屏幕阅读器隐藏的装饰图片', () => {
    const { container } = render(<SiteFlag code="DE" />);
    const img = container.querySelector('img');
    expect(img).toHaveAttribute('alt', '');
    expect(img).toHaveAttribute('aria-hidden', 'true');
  });

  it('未登记的站点不渲染，也不退回 Emoji', () => {
    const { container } = render(<SiteFlag code="FR" />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('formatSiteLabel', () => {
  it('组合中文站点名和全角括号代码，不含 Emoji', () => {
    expect(formatSiteLabel(US)).toBe('美国站（US）');
  });
});

describe('SiteLabel', () => {
  it('国旗之外保留站点名与站点码文字', () => {
    const { container } = render(<SiteLabel site={US} />);
    expect(container.querySelector('img.site-flag')).toBeInTheDocument();
    expect(container).toHaveTextContent('美国站（US）');
    expect(container.textContent).not.toMatch(/[\u{1F1E6}-\u{1F1FF}]/u);
  });
});

describe('toSiteSelectOptions', () => {
  it('以纯文本 title 保留悬停提示，值仍是站点码', () => {
    const [option] = toSiteSelectOptions([US]);
    expect(option.title).toBe('美国站（US）');
    expect(option.value).toBe('US');
  });
});
