import { useCallback, useMemo, useState } from 'react';

/**
 * 关键词「已访问」标记（issue #125）：点开某个词的亚马逊搜索页后，该词在表格里变色，
 * 免得翻页、换筛选后又把同一个词看一遍。
 *
 * **为什么不用浏览器自带的 `:visited`**：它只认 URL 历史——清历史、无痕、换域名都会丢，
 * 且浏览器出于隐私限制 `:visited` 可用的样式；需求要的是"本浏览器长期保留"，只能自己记。
 *
 * 存储与边界（都是已确认的取舍，不是疏漏）：
 * - `localStorage`，键带 `userId`：同一浏览器切账号互不继承。取不到 userId 时不读不写，
 *   绝不回退到共享键（与 `savedMode.ts` 同一条纪律）。
 * - 不跨设备、不建后端表；清站点数据 / 无痕结束即丢。
 * - 记录粒度是 `(站点, 关键词)`，**不含报表日期与视图**：同站点同词换日期、换日/周仍算已访问。
 * - **不设过期与容量淘汰**：需求是"长期保留"，静默删近期记录比多占几 KB 更糟。每条约几十字节，
 *   几万条也只有 MB 级；写入失败（配额满 / 被禁用）降级为只在本次页面内有效，不影响查询与跳转。
 */

const KEY_PREFIX = 'keywordiq.analysis.visited';
export const VISITED_VERSION = 1;

const keyFor = (userId: number) => `${KEY_PREFIX}.${userId}`;

/** 站点内关键词的规范化：去首尾空白、小写——同一个词不因大小写分成两条。 */
export function visitedKey(site: string, keyword: string): string {
  return `${site.trim().toUpperCase()}|${keyword.trim().toLowerCase()}`;
}

/** 读取已访问集合。userId 缺失 / 没存过 / JSON 损坏 / 版本不符都返回空集合（损坏的顺手丢弃）。 */
export function readVisited(userId: number | undefined): Set<string> {
  if (userId === undefined) return new Set();
  try {
    const raw = window.localStorage.getItem(keyFor(userId));
    if (!raw) return new Set();
    const parsed = JSON.parse(raw);
    if (parsed?.version !== VISITED_VERSION || !Array.isArray(parsed.keys)) {
      window.localStorage.removeItem(keyFor(userId));
      return new Set();
    }
    return new Set(parsed.keys.filter((k: unknown): k is string => typeof k === 'string'));
  } catch {
    // 存储不可读或 JSON 损坏：当作没记过。不抛——这个功能坏了不能拖垮查询。
    try {
      window.localStorage.removeItem(keyFor(userId));
    } catch {
      /* 存储本身不可用，无事可做 */
    }
    return new Set();
  }
}

/**
 * 追加一条并写回，返回写入是否成功。先重读再合并：多个标签页各自记了不同的词时，
 * 后写的一方不能把先写的一方整个覆盖掉。
 */
export function writeVisited(userId: number | undefined, key: string): boolean {
  if (userId === undefined) return false;
  try {
    const keys = readVisited(userId);
    keys.add(key);
    window.localStorage.setItem(
      keyFor(userId),
      JSON.stringify({ version: VISITED_VERSION, keys: [...keys] }),
    );
    return true;
  } catch {
    return false;
  }
}

export interface VisitedKeywords {
  isVisited: (site: string | undefined, keyword: string) => boolean;
  markVisited: (site: string | undefined, keyword: string) => void;
}

/**
 * 页面内的已访问状态。内存里的集合是权威的显示来源（写存储失败时当次仍要变色），
 * 存储只负责跨刷新 / 重开浏览器保留。
 */
export function useVisitedKeywords(userId: number | undefined): VisitedKeywords {
  const [visited, setVisited] = useState<Set<string>>(() => readVisited(userId));

  const markVisited = useCallback(
    (site: string | undefined, keyword: string) => {
      if (!site || !keyword.trim()) return;
      const key = visitedKey(site, keyword);
      setVisited((prev) => {
        if (prev.has(key)) return prev;
        const next = new Set(prev);
        next.add(key);
        return next;
      });
      writeVisited(userId, key);
    },
    [userId],
  );

  return useMemo(
    () => ({
      isVisited: (site, keyword) => !!site && visited.has(visitedKey(site, keyword)),
      markVisited,
    }),
    [visited, markVisited],
  );
}
