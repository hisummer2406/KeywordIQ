import { history, useModel } from '@umijs/max';
import { useEffect, useState } from 'react';

/**
 * 登录成功后的跳转。**登录屏与钉钉回调屏共用这一份，不要各写一份。**
 *
 * 跳转要等到 initialState 真的带上 currentUser 之后才发生。
 *
 * `await refresh()` 返回，只说明刷新动作发起了，不代表 React 已经把新的 initialState
 * 提交并重渲染。紧跟着 push 过去，ProLayout 的 onPageChange 与 access 判定读到的都还是
 * 旧值：前者判成未登录、把人推回 /login（表现为"登录成功后闪回登录页"），后者判成无
 * 权限、渲染 403。谁先谁后取决于渲染时机，所以时好时坏、慢机器更容易中。
 *
 * 用 effect 把"跳转"挂在 currentUser 到位这个事实上，竞态就不存在了——effect 在提交
 * 之后才跑，此时守卫与权限判定读到的必然是同一份新状态。
 *
 * 抽出来共用而不是复制一份：这段逻辑的正确性全在上面这段推理里，复制出去的副本会
 * 各自漂移，而它出错的表现是偶发的闪回/403，最难归因的那一类。
 */
export function useLoginRedirect() {
  const { initialState, refresh } = useModel('@@initialState');
  const [redirectTo, setRedirectTo] = useState<string | null>(null);
  const currentUser = initialState?.currentUser;

  useEffect(() => {
    if (redirectTo && currentUser) {
      history.push(redirectTo);
    }
  }, [redirectTo, currentUser]);

  /** 刷新当前用户，并在其落位后跳转到 `fallback`（或 URL 上的 redirect 参数）。 */
  const finishLogin = async (fallback = '/analysis') => {
    await refresh();
    const redirect = new URLSearchParams(history.location.search).get('redirect');
    setRedirectTo(redirect || fallback);
  };

  return { finishLogin };
}
