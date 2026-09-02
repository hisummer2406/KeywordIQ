// 最小化的 chrome.storage.local 内存替身，供测试使用。不引入额外的 Chrome API
// 测试框架——本项目只用到 storage 这一个 API 面，手写 stub 足够。
export function installChromeStub(): void {
  const store = new Map<string, unknown>();

  const storageLocal = {
    async get(keys?: string | string[] | null) {
      if (keys == null) return Object.fromEntries(store);
      const list = Array.isArray(keys) ? keys : [keys];
      const out: Record<string, unknown> = {};
      for (const key of list) {
        if (store.has(key)) out[key] = store.get(key);
      }
      return out;
    },
    async set(items: Record<string, unknown>) {
      for (const [key, value] of Object.entries(items)) store.set(key, value);
    },
    async remove(keys: string | string[]) {
      for (const key of Array.isArray(keys) ? keys : [keys]) store.delete(key);
    },
    async clear() {
      store.clear();
    },
  };

  (globalThis as { chrome?: unknown }).chrome = {
    storage: { local: storageLocal },
  };
}
