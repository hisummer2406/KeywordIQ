import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  SAVED_MODE_VERSION,
  clearSavedMode,
  readSavedMode,
  writeSavedMode,
  type SavedMode,
} from './savedMode';

const MODE: Omit<SavedMode, 'version'> = {
  site: 'DE',
  view: 'WEEK',
  filters: {
    categories: ['Home'],
    ranking: { min: 1, max: 100 },
    change: {},
    clickShare: {},
    conversionShare: {},
    isNew: undefined,
    brands: [],
    productTitle: '',
    competitorNew: false,
  },
  sort: { field: 'current_ranking', dir: 'asc' },
};

const KEY = (userId: number) => `keywordiq.analysis.savedMode.v1.${userId}`;

describe('savedMode', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('写进去能原样读出来', () => {
    expect(writeSavedMode(1, MODE)).toBe(true);
    expect(readSavedMode(1)).toEqual({ ...MODE, version: SAVED_MODE_VERSION });
  });

  it('没存过读到 null', () => {
    expect(readSavedMode(1)).toBeNull();
  });

  it('清除之后读到 null', () => {
    writeSavedMode(1, MODE);
    clearSavedMode(1);
    expect(readSavedMode(1)).toBeNull();
  });

  /**
   * 按 userId 分键是「同一浏览器换账号」那条防线。会话快照靠登出时清除来防串账号，
   * 而这份要活过登出，只能靠分键——所以这条断言就是那条防线本身。
   */
  it('甲存的乙读不到', () => {
    writeSavedMode(1, MODE);
    expect(readSavedMode(2)).toBeNull();
    expect(readSavedMode(1)).not.toBeNull();
  });

  describe('userId 缺失时不读不写', () => {
    it('不写入任何键', () => {
      expect(writeSavedMode(undefined, MODE)).toBe(false);
      expect(window.localStorage.length).toBe(0);
    });

    it('读到 null', () => {
      writeSavedMode(1, MODE);
      expect(readSavedMode(undefined)).toBeNull();
    });

    it('清除是空操作，不误删别人的', () => {
      writeSavedMode(1, MODE);
      clearSavedMode(undefined);
      expect(readSavedMode(1)).not.toBeNull();
    });
  });

  describe('坏数据一律丢弃并删掉', () => {
    it('版本不符', () => {
      window.localStorage.setItem(
        KEY(1),
        JSON.stringify({ ...MODE, version: SAVED_MODE_VERSION + 1 }),
      );

      expect(readSavedMode(1)).toBeNull();
      // 丢弃的同时要删掉，否则每次进屏都白解析一遍同一条坏数据
      expect(window.localStorage.getItem(KEY(1))).toBeNull();
    });

    it('v2 旧 payload 被丢弃——它没有 excludeBrandWords，缺失会被当成「开启」而悄悄改掉已保存的结果集', () => {
      window.localStorage.setItem(KEY(1), JSON.stringify({ ...MODE, version: 2 }));

      expect(readSavedMode(1)).toBeNull();
      expect(window.localStorage.getItem(KEY(1))).toBeNull();
    });

    it('JSON 损坏', () => {
      window.localStorage.setItem(KEY(1), '{不是 JSON');

      expect(readSavedMode(1)).toBeNull();
      expect(window.localStorage.getItem(KEY(1))).toBeNull();
    });

    it('缺关键字段（站点）', () => {
      window.localStorage.setItem(
        KEY(1),
        JSON.stringify({ version: SAVED_MODE_VERSION, view: 'DAY' }),
      );

      expect(readSavedMode(1)).toBeNull();
    });
  });

  /**
   * 隐私模式下 `setItem` 会抛。保存失败要能报出来让界面提示，但绝不能把异常抛到调用方
   * ——那会让整屏崩在一个「存个偏好」的动作上。
   */
  it('存储不可写时返回 false 而不是抛异常', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });

    expect(() => writeSavedMode(1, MODE)).not.toThrow();
    expect(writeSavedMode(1, MODE)).toBe(false);
  });

  it('存储不可读时读到 null 而不是抛异常', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });

    expect(() => readSavedMode(1)).not.toThrow();
    expect(readSavedMode(1)).toBeNull();
  });

  it('删除失败时不抛', () => {
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });

    expect(() => clearSavedMode(1)).not.toThrow();
  });
});
