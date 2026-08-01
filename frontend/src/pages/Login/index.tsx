import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { LoginFormPage, ProConfigProvider, ProFormText } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import { App } from 'antd';
import { useEffect, useState } from 'react';
import { login } from '@/services/auth';
import { setToken } from '@/utils/token';
import logoIcon from '@/assets/logo-icon.png';
import loginBg from '@/assets/login-bg.svg';
import './index.less';

/**
 * 登录屏。登录并入 SPA 路由（替代 /admin/login）。
 * 真实登录接口在阶段二落地；此处已按最终形态接好 login()，接口就绪即可用。
 */
export default function LoginPage() {
  const { message } = App.useApp();
  const { initialState, refresh } = useModel('@@initialState');
  const [redirectTo, setRedirectTo] = useState<string | null>(null);
  const currentUser = initialState?.currentUser;

  /**
   * 跳转要等到 initialState 真的带上 currentUser 之后才发生。
   *
   * `await refresh()` 返回，只说明刷新动作发起了，不代表 React 已经把新的 initialState
   * 提交并重渲染。紧跟着 push 过去，ProLayout 的 onPageChange 与 access 判定读到的都还是
   * 旧值：前者判成未登录、把人推回 /login（表现为"登录成功后闪回登录页"），后者判成无
   * 权限、渲染 403。谁先谁后取决于渲染时机，所以时好时坏、慢机器更容易中。
   *
   * 用 effect 把"跳转"挂在 currentUser 到位这个事实上，竞态就不存在了——effect 在提交
   * 之后才跑，此时守卫与权限判定读到的必然是同一份新状态。
   */
  useEffect(() => {
    if (redirectTo && currentUser) {
      history.push(redirectTo);
    }
  }, [redirectTo, currentUser]);

  const handleSubmit = async (values: API.LoginParams) => {
    try {
      const res = await login(values);
      setToken(res.token);
      await refresh();
      message.success('登录成功');
      const redirect = new URLSearchParams(history.location.search).get('redirect');
      setRedirectTo(redirect || '/analysis');
    } catch (e: any) {
      // login() 显式声明了 skipErrorHandler（凭证错误不该弹全局提示），因此这里必须
      // 自己呈现——「显式跳过」与「必须自行呈现」是一对，缺一就回到了静默吞错误。
      message.error(e?.response?.data?.message || '用户名或密码错误');
    }
  };

  return (
    <ProConfigProvider hashed={false}>
      {/*
        LoginFormPage 内部靠 height:100% 逐层撑开，所以**父级必须给出确定高度**。
        原先把 minHeight:100vh 挂在它自己身上不成立：min-height 不构成子元素 height:100%
        的参照，内层那行 flex 会退回内容高度（398px），卡片于是贴死在页面顶部。
        外面套一层定高容器，组件自身再取满，垂直居中才成立。
      */}
      <div className="login-page-wrapper" style={{ height: '100vh' }}>
        <LoginFormPage
          style={{ height: '100%' }}
          backgroundImageUrl={loginBg}
          logo={<img src={logoIcon} alt="海眸系统" style={{ height: 44 }} />}
          title="海眸系统"
          subTitle="Amazon 搜索词数据分析平台"
          onFinish={handleSubmit as any}
        >
          <ProFormText
            name="username"
            fieldProps={{ size: 'large', prefix: <UserOutlined /> }}
            placeholder="用户名"
            rules={[{ required: true, message: '请输入用户名' }]}
          />
          <ProFormText.Password
            name="password"
            fieldProps={{ size: 'large', prefix: <LockOutlined /> }}
            placeholder="密码"
            rules={[{ required: true, message: '请输入密码' }]}
          />
        </LoginFormPage>
      </div>
    </ProConfigProvider>
  );
}
