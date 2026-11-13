import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import AnswerMarkdown from './AnswerMarkdown';

/**
 * 回答的受限 Markdown（change refine-opportunity-chat-ui 决策 D2；admin-spa「对话类界面的组件与安全边界」
 * 场景「回答含 HTML 或脚本」「回答含链接或图片语法」「回答含标题、列表与表格」）。
 */

const show = (md: string) => render(<AnswerMarkdown>{md}</AnswerMarkdown>).container;

describe('受限 Markdown：注入面', () => {
  it('块级 script 按字面显示，不产生元素', () => {
    const md = '<script>window.__x=1</script>';
    const c = show(md);
    expect(c.querySelector('script')).toBeNull();
    expect(c.textContent).toContain(md);
    expect((window as any).__x).toBeUndefined();
  });

  it('行内 HTML 按字面显示，不产生元素', () => {
    const md = '摘要<img src="x" onerror="window.__y=1">结尾 <b>粗</b>';
    const c = show(md);
    expect(c.querySelector('img')).toBeNull();
    expect(c.querySelector('b')).toBeNull();
    expect(c.textContent).toContain('<img src="x" onerror="window.__y=1">');
    expect(c.textContent).toContain('<b>粗</b>');
  });

  it('链接只留文字，危险协议与普通地址都不产生 <a>', () => {
    const c = show('见[官网](javascript:alert(1))、[文档](https://e.com/doc) 与 https://e.com/raw');
    expect(c.querySelector('a')).toBeNull();
    expect(c.textContent).toContain('官网');
    expect(c.textContent).toContain('文档');
    expect(c.textContent).not.toContain('javascript:');
  });

  it('引用式链接与图片同样降级', () => {
    const c = show('看[这里][r]和![示意][i]\n\n[r]: https://e.com\n[i]: https://e.com/a.png');
    expect(c.querySelector('a')).toBeNull();
    expect(c.querySelector('img')).toBeNull();
    expect(c.textContent).toContain('这里');
    expect(c.textContent).toContain('示意');
  });

  it('图片只显示替代文字，不发起图片请求', () => {
    const c = show('![外观示意图](https://e.com/a.png)');
    expect(c.querySelector('img')).toBeNull();
    expect(c.textContent).toContain('外观示意图');
  });

  it('没有任何元素带 href 或 src', () => {
    const c = show('[a](https://e.com) ![b](https://e.com/b.png) <a href="https://e.com">c</a>');
    expect(c.querySelector('[href],[src]')).toBeNull();
  });
});

describe('受限 Markdown：排版', () => {
  it('标题、列表、表格按结构渲染，表格包在横向滚动容器里', () => {
    const c = show('## 结论\n\n- 第一条\n- 第二条\n\n1. 甲\n2. 乙\n\n| 专利 | 相似度 |\n|---|---|\n| US D1 | 高 |');
    expect(c.querySelector('h2')?.textContent).toBe('结论');
    expect(c.querySelectorAll('ul > li')).toHaveLength(2);
    expect(c.querySelectorAll('ol > li')).toHaveLength(2);
    expect(c.querySelector('.chat-answer__table > table td')?.textContent).toBe('US D1');
  });

  it('粗体、引用、代码保留', () => {
    const c = show('**重点**\n\n> 引用一句\n\n`TTL:(mug)`');
    expect(c.querySelector('strong')?.textContent).toBe('重点');
    expect(c.querySelector('blockquote')?.textContent).toContain('引用一句');
    expect(c.querySelector('code')?.textContent).toBe('TTL:(mug)');
  });

  it('段内单个换行保留在文本里（由 pre-line 显示为换行）', () => {
    const c = show('第一行\n第二行');
    expect(c.querySelector('p')?.textContent).toBe('第一行\n第二行');
  });
});
