/**
 * 顶栏右侧的账号区。
 *
 * plugin-layout 内置的 rightRender 只渲染「头像 + 用户名」加一个「退出登录」，
 * 不显示角色。本系统是 RBAC（admin / operator / analyst 权限点各不同），看不到自己
 * 的身份时，某个按钮灰着就分不清是没权限还是坏了——故自定义。
 *
 * 角色中文名取自后端 `role_name`（与 role 表同源），前端不维护 code → 名称的映射：
 * 那份映射会随角色增删静默漂移。
 */
import {
  IdcardOutlined,
  LogoutOutlined,
  SafetyCertificateOutlined,
  UserOutlined,
} from '@ant-design/icons';
import { Avatar, Dropdown, Space, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';

/** 角色码 → 标签配色。取不到就用中性色，不阻断显示。 */
const ROLE_COLOR: Record<string, string> = {
  admin: 'red',
  operator: 'blue',
  analyst: 'green',
};

export interface RightContentProps {
  currentUser?: API.CurrentUser;
  onLogout: () => void;
}

export default function RightContent({ currentUser, onLogout }: RightContentProps) {
  if (!currentUser) return null;

  const roleLabel = currentUser.role_name || currentUser.role || '未分配角色';
  const roleColor = currentUser.role ? ROLE_COLOR[currentUser.role] : undefined;
  const permissionCount = currentUser.permissions?.length ?? 0;

  const items: MenuProps['items'] = [
    {
      key: 'profile',
      // 只读的身份信息块。disabled 是为了让它不可点——这里没有「个人中心」页面，
      // 放一个跳不动的入口比不放更糟。
      disabled: true,
      label: (
        <Space direction="vertical" size={2} style={{ padding: '4px 0' }}>
          <Space size={6}>
            <IdcardOutlined />
            <Typography.Text strong>{currentUser.username}</Typography.Text>
          </Space>
          <Space size={6}>
            <SafetyCertificateOutlined style={{ color: '#8c8c8c' }} />
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              {roleLabel}
              {currentUser.is_super ? ' · 超级账号' : ''} · {permissionCount} 项权限
            </Typography.Text>
          </Space>
        </Space>
      ),
    },
    { type: 'divider' },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: onLogout,
    },
  ];

  return (
    <Dropdown menu={{ items }} placement="bottomRight" trigger={['click']}>
      <Space size={8} style={{ cursor: 'pointer', padding: '0 12px' }}>
        <Avatar size="small" icon={<UserOutlined />} style={{ backgroundColor: '#1677ff' }} />
        <Typography.Text>{currentUser.username}</Typography.Text>
        <Tag color={roleColor} style={{ marginInlineEnd: 0 }}>
          {roleLabel}
        </Tag>
      </Space>
    </Dropdown>
  );
}
