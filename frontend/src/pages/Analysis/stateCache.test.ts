import { beforeEach, describe, expect, it } from 'vitest';
import {
  CACHE_VERSION,
  clearSnapshot,
  readSnapshot,
  sameRequest,
  writeSlot,
  writeValue,
  __setRawSnapshotForTest,
  type RequestKey,
} from './stateCache';
import type { SearchBody } from '@/services/analysis';

const body = (over: Partial<SearchBody> = {}): SearchBody =>
  ({ site: 'US', view: 'DAY', date: '2026-08-08', ...over }) as SearchBody;

const reqKey = (over: Partial<RequestKey> = {}): RequestKey => ({
  body: body(),
  orderBy: 'current_ranking',
  orderDir: 'asc',
  page: 1,
  perPage: 50,
  ...over,
});

beforeEach(() => clearSnapshot());

describe('快照读写', () => {
  it('没写过时读回空', () => {
    expect(readSnapshot()).toBeNull();
  });

  it('写入后能原样读回', () => {
    writeValue('appliedSig', 'sig-1');
    writeSlot({ applied: body(), rows: [{ keyword_id: 7 }] as any });

    const snap = readSnapshot();
    expect(snap?.values.appliedSig).toBe('sig-1');
    expect(snap?.applied).toEqual(body());
    expect(snap?.rows).toHaveLength(1);
  });

  it('多次写入同一个键取最后一次', () => {
    writeValue('appliedSig', 'sig-1');
    writeValue('appliedSig', 'sig-2');
    expect(readSnapshot()?.values.appliedSig).toBe('sig-2');
  });

  it('clearSnapshot 之后读回空', () => {
    writeValue('appliedSig', 'sig-1');
    clearSnapshot();
    expect(readSnapshot()).toBeNull();
  });

  it('版本号不匹配视同没有缓存', () => {
    writeValue('appliedSig', 'sig-1');
    // 模拟「代码升级后状态形状变了，但会话里还留着旧快照」
    __setRawSnapshotForTest({ ...readSnapshot()!, version: CACHE_VERSION + 1 });
    expect(readSnapshot()).toBeNull();
  });
});

describe('sameRequest', () => {
  it('两次完全相同 → true', () => {
    expect(sameRequest(reqKey(), reqKey())).toBe(true);
  });

  it('条件对象键序不同但内容相同 → true', () => {
    const a = reqKey({ body: { site: 'US', view: 'DAY', date: '2026-08-08' } as SearchBody });
    const b = reqKey({ body: { date: '2026-08-08', view: 'DAY', site: 'US' } as SearchBody });
    expect(sameRequest(a, b)).toBe(true);
  });

  it.each([
    ['页码', { page: 2 }],
    ['每页条数', { perPage: 100 }],
    ['排序字段', { orderBy: 'ranking_change' }],
    ['排序方向', { orderDir: 'desc' as const }],
  ])('%s 不同 → false', (_label, over) => {
    expect(sameRequest(reqKey(), reqKey(over))).toBe(false);
  });

  it('生效条件不同 → false', () => {
    expect(sameRequest(reqKey(), reqKey({ body: body({ date: '2026-08-07' }) }))).toBe(false);
  });

  it('筛选字段多一项 → false', () => {
    expect(sameRequest(reqKey(), reqKey({ body: body({ ranking_max: 100 }) }))).toBe(false);
  });

  it('任一侧为空 → false', () => {
    expect(sameRequest(null, reqKey())).toBe(false);
    expect(sameRequest(reqKey(), null)).toBe(false);
    expect(sameRequest(null, null)).toBe(false);
  });
});
