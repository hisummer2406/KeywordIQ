/**
 * 整页跳转。
 *
 * 单独成一个模块只为一件事：**让它可被测试替换**。直接写 `window.location.href = url`
 * 在 jsdom 里既拦不住也断言不了，测试只能退而求其次去断言"取到了地址"，那就漏掉了
 * 「到底有没有跳过去」这一半。
 *
 * 用它而不是 `history.push`：目标是钉钉的外域地址，SPA 路由跳不过去——push 只会在
 * 自己的路由表里找不到而落到 404 屏。
 */
export function hardRedirect(url: string): void {
  window.location.href = url;
}
