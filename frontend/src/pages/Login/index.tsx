import { LockOutlined, UserOutlined } from '@ant-design/icons';
import { LoginForm, ProConfigProvider, ProFormText } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import { App } from 'antd';
import { login } from '@/services/auth';
import { TOKEN_KEY } from '@/app';

/**
 * 登录屏。登录并入 SPA 路由（替代 /admin/login）。
 * 真实登录接口在阶段二落地；此处已按最终形态接好 login()，接口就绪即可用。
 */
export default function LoginPage() {
  const { message } = App.useApp();
  const { refresh } = useModel('@@initialState');

  const handleSubmit = async (values: API.LoginParams) => {
    try {
      const res = await login(values);
      localStorage.setItem(TOKEN_KEY, res.token);
      await refresh();
      message.success('登录成功');
      const redirect = new URLSearchParams(history.location.search).get('redirect');
      history.push(redirect || '/analysis');
    } catch {
      // 错误提示由全局 errorHandler 统一处理
    }
  };

  return (
    <ProConfigProvider hashed={false}>
      <div style={{ display: 'flex', minHeight: '100vh', alignItems: 'center' }}>
        <LoginForm title="KeywordIQ" subTitle="Amazon 搜索词数据分析平台" onFinish={handleSubmit as any}>
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
        </LoginForm>
      </div>
    </ProConfigProvider>
  );
}
