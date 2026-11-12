import { history } from '@umijs/max';
import { App, Button, Result, Spin } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { useLoginRedirect } from '@/hooks/useLoginRedirect';
import { ssoCallback } from '@/services/auth';
import { setToken } from '@/utils/token';

/**
 * 钉钉扫码回调屏。用户刚从钉钉跳回来，落在这里。
 *
 * 三条路径都必须**明确**——这一屏什么都不说，等同于把用户丢在半路：他既不知道该等
 * 还是该重试，也找不到回登录页的入口。
 *
 * 令牌走的是普通 JSON 响应（`POST /api/auth/sso/callback` 返回 `{token, user}`，与
 * `POST /api/auth/token` 完全同形），**不经 URL**：URL 会进浏览器历史、Referer 和
 * nginx access log。
 */
export default function SsoCallbackPage() {
  const { message } = App.useApp();
  const { finishLogin } = useLoginRedirect();
  const [failed, setFailed] = useState<string | null>(null);
  // 回调只该跑一次。effect 在 StrictMode 下会执行两次，而 authCode 是一次性的——
  // 第二次必然拿到「授权码已失效」，把一次成功的登录显示成失败。
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;

    const params = new URLSearchParams(history.location.search);
    const error = params.get('error');
    const authCode = params.get('authCode');
    const state = params.get('state');

    if (error) {
      // 钉钉侧拒绝或用户取消授权。不带 authCode，没有任何可换的东西。
      setFailed('钉钉未授权本次登录，请重试或改用密码登录。');
      return;
    }
    if (!authCode || !state) {
      // 直接敲地址进来的。没什么可做，也没必要报错，回登录页即可。
      history.push('/login');
      return;
    }

    ssoCallback({ auth_code: authCode, state })
      .then(async (res) => {
        setToken(res.token);
        message.success('登录成功');
        await finishLogin('/analysis');
      })
      .catch(() => {
        // 具体失败原因由全局 errorHandler 呈现（这个调用没有 skipErrorHandler）。
        // 这里只负责让用户有路可走——否则他停在一个转圈的页面上。
        setFailed('登录未能完成，请返回登录页重试。');
      });
    // 只跑一次，依赖刻意留空（started 已经兜住重复执行）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (failed) {
    return (
      <Result
        status="warning"
        title="钉钉登录未完成"
        subTitle={failed}
        extra={
          <Button type="primary" onClick={() => history.push('/login')}>
            返回登录
          </Button>
        }
      />
    );
  }

  return (
    <div style={{ display: 'flex', height: '100vh', alignItems: 'center', justifyContent: 'center' }}>
      <Spin size="large" description="正在完成钉钉登录…">
        <div style={{ padding: 48 }} />
      </Spin>
    </div>
  );
}
