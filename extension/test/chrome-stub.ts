// 最小化的 chrome.storage.local / chrome.permissions 内存替身，供测试使用。
// 不引入额外的 Chrome API 测试框架——本项目只用到这两个 API 面，手写 stub 足够。
//
// permissions.request 默认直接放行（返回 true）：多数测试关心的是"保存流程会
// 申请权限"而非"用户拒绝时怎么办"，后者由 host-permission.test.ts /
// options.test.ts 里单独 `chrome.permissions.request = vi.fn()...` 覆盖。
export function installChromeStub(): void {
  const store = new Map<string, unknown>();
  const grantedOrigins = new Set<string>();

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

  const permissions = {
    async contains({ origins }: { origins: string[] }) {
      return origins.every((origin) => grantedOrigins.has(origin));
    },
    async request({ origins }: { origins: string[] }) {
      for (const origin of origins) grantedOrigins.add(origin);
      return true;
    },
    async remove({ origins }: { origins: string[] }) {
      for (const origin of origins) grantedOrigins.delete(origin);
      return true;
    },
  };

  (globalThis as { chrome?: unknown }).chrome = {
    storage: { local: storageLocal },
    permissions,
  };
}
