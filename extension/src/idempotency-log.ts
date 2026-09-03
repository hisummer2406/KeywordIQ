// 409 幂等冲突理论上不该发生（decision 3 的结构保证幂等键与请求体一对一绑定）。
// 一旦发生说明是插件自身缺陷，把原始响应体存进一个有界环形缓冲区，供运营截图
// 反馈问题（design.md 决策 5 / tasks.md 6.4）。写失败（如存储配额）不影响上报
// 结果本身，调用方应吞掉异常。

const LOG_KEY = 'keywordiq_idempotency_conflict_log';
const MAX_ENTRIES = 20;

export interface IdempotencyConflictLogEntry {
  loggedAt: string;
  requestId: string;
  status: number;
  responseBody: unknown;
}

export async function appendIdempotencyConflictLog(
  entry: Omit<IdempotencyConflictLogEntry, 'loggedAt'>,
): Promise<void> {
  const stored = await chrome.storage.local.get(LOG_KEY);
  const existing = Array.isArray(stored[LOG_KEY])
    ? (stored[LOG_KEY] as IdempotencyConflictLogEntry[])
    : [];
  const next = [...existing, { ...entry, loggedAt: new Date().toISOString() }].slice(-MAX_ENTRIES);
  await chrome.storage.local.set({ [LOG_KEY]: next });
}
