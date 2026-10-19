import { beforeEach, describe, expect, it } from 'vitest';
import { readVisited, visitedKey, VISITED_VERSION, writeVisited } from './visitedKeywords';

describe('visitedKeywords', () => {
  beforeEach(() => window.localStorage.clear());

  it('键按站点区分，大小写与首尾空白不影响同一个词', () => {
    expect(visitedKey('us', ' Power Bank ')).toBe(visitedKey('US', 'power bank'));
    expect(visitedKey('US', 'power bank')).not.toBe(visitedKey('DE', 'power bank'));
  });

  it('写入后可读回，且按用户隔离', () => {
    expect(writeVisited(1, visitedKey('US', 'a'))).toBe(true);
    expect(readVisited(1).has(visitedKey('US', 'a'))).toBe(true);
    expect(readVisited(2).size).toBe(0);
  });

  it('userId 缺失时不读不写', () => {
    expect(writeVisited(undefined, 'x')).toBe(false);
    expect(readVisited(undefined).size).toBe(0);
    expect(window.localStorage.length).toBe(0);
  });

  it('追加时与其他标签页已写入的记录合并，而不是覆盖', () => {
    writeVisited(1, 'US|a');
    // 另一个标签页在本页不知情时写入了 b
    window.localStorage.setItem(
      'keywordiq.analysis.visited.1',
      JSON.stringify({ version: VISITED_VERSION, keys: ['US|a', 'US|b'] }),
    );
    writeVisited(1, 'US|c');
    expect([...readVisited(1)].sort()).toEqual(['US|a', 'US|b', 'US|c']);
  });

  it('JSON 损坏或版本不符时降级为空集合并丢弃，不抛', () => {
    window.localStorage.setItem('keywordiq.analysis.visited.1', '{坏的');
    expect(readVisited(1).size).toBe(0);
    window.localStorage.setItem(
      'keywordiq.analysis.visited.1',
      JSON.stringify({ version: 999, keys: ['US|a'] }),
    );
    expect(readVisited(1).size).toBe(0);
    expect(window.localStorage.getItem('keywordiq.analysis.visited.1')).toBeNull();
  });

  it('存储不可写时返回 false 而不抛', () => {
    const orig = Storage.prototype.setItem;
    Storage.prototype.setItem = () => {
      throw new Error('quota');
    };
    try {
      expect(writeVisited(1, 'US|a')).toBe(false);
    } finally {
      Storage.prototype.setItem = orig;
    }
  });
});
