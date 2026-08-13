/**
 * 顶栏右侧的账号区：触发器（头像 + 用户名 + 角色标签）+ 下拉（可点的操作）。
 *
 * 自定义而不用 plugin-layout 内置的 rightRender，是因为内置的只有「头像 + 用户名 +
 * 退出登录」，**不显示角色**。本系统是 RBAC，看不到自己的身份时，某个按钮灰着就分不清
 * 是没权限还是坏了——那个角色标签就在触发器上，一直可见。
 *
 * 下拉里只放能点的操作，不放只读信息：身份已经在触发器上了，在下拉里再写一遍是第二遍。
 *
 * 角色中文名取自后端 `role_name`（与 role 表同源），前端不维护 code → 名称的映射：
 * 那份映射会随角色增删静默漂移。
 */
import { LogoutOutlined, TeamOutlined, UserOutlined } from '@ant-design/icons';
import { history } from '@umijs/max';
import { Avatar, Dropdown, Space, Tag, Typography } from 'antd';
import type { MenuProps } from 'antd';

/**
 * 深色渐变顶栏上的「玻璃」样式：半透白底 + 白字。
 *
 * 这里刻意**不再按角色配色**（曾经 admin 红 / operator 蓝 / analyst 绿）。顶栏改成蓝青
 * 渐变后，任何实色标签都在跟背景抢注意力——红底标签压在青色上尤其刺眼。而角色本来就
 * 以中文全称写着（「超级管理员」），颜色不承载任何它没说的信息，是纯装饰；装饰与背景
 * 冲突时删掉装饰。
 */
const GLASS: React.CSSProperties = {
  background: 'rgba(255, 255, 255, 0.18)',
  borderColor: 'rgba(255, 255, 255, 0.35)',
  color: '#ffffff',
};

export interface RightContentProps {
  currentUser?: API.CurrentUser;
  onLogout: () => void;
}

export default function RightContent({ currentUser, onLogout }: RightContentProps) {
  if (!currentUser) return null;

  const roleLabel = currentUser.role_name || currentUser.role || '未分配角色';

  // 下拉里**只放能点的东西**。此前第一项是一整块只读身份信息（用户名 + 角色 + 超级账号
  // 标记 + 权限计数），已删除：用户名与角色在触发器上就显示着，下拉里是第二遍；剩下的
  // 「超级账号 / N 项权限」对使用者不构成任何可执行的信息。
  //
  // 它原本承担的作用——RBAC 下让人看得见自己的身份，好把「没权限」和「坏了」区分开
  // ——现在由顶栏那个角色标签承担，没有丢失。
  const items: MenuProps['items'] = [
    // 用户管理：只有超级账号看得见。
    //
    // 它不在顶栏菜单里（.umirc.ts 给 /user 加了 hideInMenu）——低频的管理动作，与
    // 「选品 / 上传」两条日常主线不同层次，放进账号区比摆在顶栏更贴合它的使用频率。
    //
    // 这里的 is_super 判据与后端 `simple_auth.require_super()` 同源。**隐藏入口不是
    // 访问控制**：真正拦住的是路由 access 守卫与端点依赖，这里只是不给一个点了会 403
    // 的入口。
    ...(currentUser.is_super
      ? [
          {
            key: 'user-manage',
            icon: <TeamOutlined />,
            label: '用户管理',
            onClick: () => history.push('/user'),
          },
          // 与退出登录之间隔一道：一个是日常跳转，一个是会话终止，挨着容易点错
          { type: 'divider' as const },
        ]
      : []),
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录',
      onClick: onLogout,
    },
  ];

  return (
    <Dropdown menu={{ items }} placement="bottomRight" trigger={['click']}>
      {/* 顶栏是深色渐变，这一整块必须自己反色：ProLayout 的 header token 只管它内置的
          右侧操作项，管不到 rightContentRender 里的自定义内容。漏了的表现是深底上一块
          几乎读不出来的深色文字。 */}
      <Space size={8} style={{ cursor: 'pointer', padding: '0 12px' }}>
        <Avatar size="small" icon={<UserOutlined />} style={GLASS} />
        <Typography.Text style={{ color: '#ffffff' }}>{currentUser.username}</Typography.Text>
        <Tag style={{ ...GLASS, marginInlineEnd: 0 }}>{roleLabel}</Tag>
      </Space>
    </Dropdown>
  );
}
