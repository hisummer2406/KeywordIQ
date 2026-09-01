import { DingtalkOutlined, LockOutlined, UserOutlined } from '@ant-design/icons';
import { LoginFormPage, ProConfigProvider, ProFormText } from '@ant-design/pro-components';
import { App, Button, Divider } from 'antd';
import { useEffect, useState } from 'react';
import { useLoginRedirect } from '@/hooks/useLoginRedirect';
import { login, ssoAuthorizeUrl, ssoStatus } from '@/services/auth';
import { hardRedirect } from '@/utils/redirect';
import { setToken } from '@/utils/token';
import logoIcon from '@/assets/logo-icon.png';
import loginBg from '@/assets/login-bg.svg';
import './index.less';

/**
 * 登录屏。登录并入 SPA 路由（替代 /admin/login）。
 * 真实登录接口在阶段二落地；此处已按最终形态接好 login()，接口就绪即可用。
 */
/**
 * 登录失败的文案。**按失败类型分开说，不能一律归成「账号或密码错误」**：
 * 「服务挂了」与「你输错了」要求用户做的动作完全相反——前者该等恢复或找运维，后者该
 * 重输密码。把前者说成后者，用户只会一遍遍试密码（将来若加了失败锁定，这一条会直接
 * 变成把自己锁出去）。这不是假想：曾因 devServer 代理端口配错，连试五次密码、又去核对
 * 种子脚本口令和库里的散列，最后才从代理日志里看出请求根本没到后端（issue #58）。
 *
 * 判据是**有没有拿到响应**，不是有没有 message。网络失败、超时都没有 response；
 * nginx 502 这类有 response 但响应体不是统一错误契约，此时同样不能落到凭证文案上。
 * 有契约 message 就直接展示——按错误契约，那句话本就是面向用户的。
 */
export function loginErrorMessage(e: any): string {
  // 没有响应 = 请求压根没到后端（连接被拒、DNS、超时、跨域被拦）
  if (!e?.response) return '无法连接服务器，请检查网络后重试';

  const status = e.response.status;
  const detail: string | undefined = e.response.data?.message;
  // 401 是唯一「凭证确实不对」的状态码
  if (status === 401) return detail || '用户名或密码错误';
  return detail || `服务暂时不可用，请稍后重试（${status}）`;
}

export default function LoginPage() {
  const { message } = App.useApp();
  const { finishLogin } = useLoginRedirect();
  const [ssoEnabled, setSsoEnabled] = useState(false);
  const [ssoPending, setSsoPending] = useState(false);

  /**
   * 探测 SSO 是否可用，决定要不要渲染钉钉按钮。
   *
   * 探测失败按「不可用」处理：用户还有密码登录这条路，为一次探测弹红条只会让他困惑。
   * 这也是为什么 `ssoStatus` 声明了 skipErrorHandler——静默是显式的，兜底在这里。
   */
  useEffect(() => {
    let alive = true;
    ssoStatus()
      .then((res) => {
        if (alive) setSsoEnabled(Boolean(res?.enabled));
      })
      .catch(() => {
        if (alive) setSsoEnabled(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  const handleSsoLogin = async () => {
    setSsoPending(true);
    try {
      const { url } = await ssoAuthorizeUrl();
      // 整页跳转：目标是钉钉外域，history.push 只会在自己的路由表里找不到而落到 404。
      hardRedirect(url);
    } catch {
      // 取地址失败由全局 errorHandler 呈现（这个调用没有 skipErrorHandler）。
      setSsoPending(false);
    }
  };


  const handleSubmit = async (values: API.LoginParams) => {
    try {
      const res = await login(values);
      setToken(res.token);
      message.success('登录成功');
      // 跳转的竞态处理在 useLoginRedirect 里，与钉钉回调屏共用同一份。
      await finishLogin('/analysis');
    } catch (e: any) {
      // login() 显式声明了 skipErrorHandler（凭证错误不该弹全局提示），因此这里必须
      // 自己呈现——「显式跳过」与「必须自行呈现」是一对，缺一就回到了静默吞错误。
      message.error(loginErrorMessage(e));
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
          actions={
            ssoEnabled ? (
              <>
                <Divider plain style={{ fontSize: 12, color: 'rgba(0,0,0,.45)' }}>
                  其他登录方式
                </Divider>
                <Button
                  block
                  size="large"
                  icon={<DingtalkOutlined />}
                  loading={ssoPending}
                  onClick={handleSsoLogin}
                >
                  钉钉登录
                </Button>
              </>
            ) : null
          }
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
