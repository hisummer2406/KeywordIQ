/** JWT 在 localStorage 的存取。独立成模块——src/app.tsx 是 Umi 运行时配置文件，
 * 只允许导出白名单 key（getInitialState/layout/request 等），常量不能从那里导出。 */
export const TOKEN_KEY = 'keywordiq_token';

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string): void {
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken(): void {
  localStorage.removeItem(TOKEN_KEY);
}
