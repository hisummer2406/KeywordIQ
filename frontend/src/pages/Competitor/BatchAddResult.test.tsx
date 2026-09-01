import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import BatchAddResult from './BatchAddResult';

function result(overrides: Record<string, unknown> = {}) {
  return {
    success: true,
    added: [],
    already_watched: [],
    rejected: [],
    ...overrides,
  } as never;
}

describe('失败必须逐条列出原因', () => {
  it('每一条都显示自己的原因，不是只报条数', () => {
    render(
      <BatchAddResult
        result={result({
          rejected: [
            { input: 'a.example', reason: '这个站点不是 Shopify 建站，暂时无法监控' },
            { input: 'b.example', reason: '无法访问该站点，请确认域名是否正确' },
          ],
        })}
      />,
    );

    // 「2 条失败」等于让用户自己去猜是哪几条、为什么——而这两类对应的动作完全不同
    // 用完整原因文本断言：Alert 的说明里也会出现「不是 Shopify」这几个字
    expect(screen.getByText('这个站点不是 Shopify 建站，暂时无法监控')).toBeTruthy();
    expect(screen.getByText('无法访问该站点，请确认域名是否正确')).toBeTruthy();
    expect(screen.getByText('a.example')).toBeTruthy();
    expect(screen.getByText('b.example')).toBeTruthy();
  });

  it('回指的是用户粘的原文那一行，不是规范化后的域名', () => {
    const raw = 'HTTPS://WWW.Example.com/products/abc?utm_source=fb';
    render(<BatchAddResult result={result({ rejected: [{ input: raw, reason: '不受支持' }] })} />);

    // 只回 example.com 的话，用户无法把结果与自己粘的内容对上
    expect(screen.getByText(raw)).toBeTruthy();
  });

  it('全批失败时额外给一条指路，不让用户对着空结果发呆', () => {
    render(<BatchAddResult result={result({ rejected: [{ input: 'x', reason: '不受支持' }] })} />);

    expect(screen.getByText('这一批都没能加入')).toBeTruthy();
  });
});

describe('三段计数', () => {
  it('三段各自计数，互不混淆', () => {
    render(
      <BatchAddResult
        result={result({
          added: [{ input: 'a', domain: 'a.example', name: '甲店', currency: 'USD' }],
          already_watched: [{ input: 'b', domain: 'b.example', name: null, currency: null }],
          rejected: [{ input: 'c', reason: '不受支持' }],
        })}
      />,
    );

    expect(screen.getByText('已加入 1')).toBeTruthy();
    expect(screen.getByText('已关注过 1')).toBeTruthy();
    expect(screen.getByText('未能加入 1')).toBeTruthy();
  });

  it('没有失败时不出现失败段', () => {
    render(
      <BatchAddResult
        result={result({
          added: [{ input: 'a', domain: 'a.example', name: '甲店', currency: 'USD' }],
        })}
      />,
    );

    expect(screen.queryByText(/未能加入/)).toBeNull();
    expect(screen.queryByText('这一批都没能加入')).toBeNull();
  });
});
