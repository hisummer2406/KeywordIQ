/** JWT 在 localStorage 的存取。独立成模块——src/app.tsx 是 Umi 运行时配置文件，
 * 只允许导出白名单 key（getInitialState/layout/request 等），常量不能从那里导出。 */
export const TOKEN_KEY = 'keywordiq_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** 只读 JWT payload 里的 `iat`（签发时刻），不校验签名——客户端本来就没有密钥验证它，
 * 这里只是借它做一个先后顺序的启发式判断（见 setToken 的说明），不是鉴权。解不出来
 * （包括测试里塞的非 JWT 假字符串）一律返回 null，调用方按"没有可比的顺序信息"处理。 */
function decodedIat(token: string): number | null {
  try {
    const payload = token.split('.')[1];
    if (!payload) return null;
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    const iat = JSON.parse(json)?.iat;
    return typeof iat === 'number' ? iat : null;
  } catch {
    return null;
  }
}

/**
 * 写入令牌。**不是无条件覆盖**：若当前已存有一张令牌，且新旧两张都能解出 `iat`，
 * 新令牌比已存的更旧则跳过这次写入——这是同一账号并发登录（两个标签页/设备同时
 * 登录同一账号）时唯一能识别"这次响应其实是输家"的线索（issue #89，PR #91 复审
 * 第七轮）：数据库按提交顺序串行决定"最后一次生效"（design.md 决策 5），但两次登录
 * 各自的 HTTP 响应完全可能反序抵达浏览器——赢家的响应先到、把赢家令牌存好，输家的
 * 响应后到；若这里无条件覆盖，会把赢家令牌换成一张服务端早已判定失效的令牌，导致
 * 两个标签页随后都被判会话失效、一起被踢下线，即便服务端明明还有一个有效会话。
 * `iat` 由 `AuthService.create_access_token` 紧跟在 `rotate_active_session` 提交
 * 之后生成，因此与数据库提交顺序同向，用它比较不必依赖响应到达顺序。
 *
 * 任一令牌解不出 `iat`（含测试里塞的非 JWT 假字符串）时不做这个判断，直接覆盖——
 * 退回原来"无条件覆盖"的行为，这不是本函数要兜底的场景。
 */
export function setToken(token: string): void {
  const current = getToken();
  if (current) {
    const currentIat = decodedIat(current);
    const nextIat = decodedIat(token);
    if (currentIat !== null && nextIat !== null && nextIat < currentIat) {
      return;
    }
  }
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}

/**
 * 这次失败请求发起时用的令牌（`usedToken`）是否已经不是当前本地持有的令牌——即
 * "陈旧响应"：本浏览器在它发出之后又完成过一次新登录，这是一个迟到的、属于旧令牌
 * 的响应，不该反过来清掉新会话（issue #89）。
 *
 * **只有"当前仍存在另一张有效令牌"且与它不同时才算陈旧。** 当前令牌若是 `null`
 * （本浏览器已经在别处主动退出、本地已无令牌），这不是陈旧响应，是真实的会话
 * 结束——调用方必须继续按正常失败处理（清本地状态、跳登录页），不能在这里被拦下；
 * 没有任何 storage 事件监听会替调用方补做这件事（PR #91 复审第七轮）。
 *
 * `app.tsx` 的全局 `errorHandler` 与 `exportAnalysis`（裸 fetch，绕过 errorHandler）
 * 共用这条判断——此前两处各写一份，都只判断"是否不同"，没有排除"变成 null"这种
 * 情形，让"另一个标签页刚退出"被误判成"另一个标签页刚登录"。
 */
export function isStaleAuthResponse(usedToken: string | null): boolean {
  const currentToken = getToken();
  return Boolean(currentToken) && usedToken !== currentToken;
}
