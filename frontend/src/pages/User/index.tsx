import { PageContainer } from '@ant-design/pro-components';
import { Alert } from 'antd';

/**
 * 用户管理屏（占位）。
 * 阶段六用 ProTable/ProForm 复刻用户增删改查与角色分配，接阶段二自建鉴权模型的后端。
 */
export default function UserPage() {
  return (
    <PageContainer title="用户管理">
      <Alert
        type="info"
        message="脚手架占位"
        description="阶段六实现：ProTable/ProForm 复刻用户增删改查、角色分配，接自建鉴权后端。"
      />
    </PageContainer>
  );
}
