import { PlusOutlined } from '@ant-design/icons';
import {
  ModalForm,
  PageContainer,
  ProFormSelect,
  ProFormSwitch,
  ProFormText,
  ProTable,
} from '@ant-design/pro-components';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { App, Button, Tag } from 'antd';
import { useEffect, useRef, useState } from 'react';
import {
  createUser,
  fetchUsers,
  getRoles,
  toggleUserStatus,
  updateUser,
  type RoleOption,
  type UserRow,
} from '@/services/user';

/** 用户管理屏（阶段六）。ProTable 列表 + ModalForm 增改 + 角色分配 + 启用/禁用。 */
export default function UserPage() {
  const { message, modal } = App.useApp();
  const actionRef = useRef<ActionType>();
  const [roles, setRoles] = useState<RoleOption[]>([]);
  const [editing, setEditing] = useState<UserRow | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  useEffect(() => {
    getRoles().then(setRoles).catch(() => undefined);
  }, []);

  const roleOptions = roles.map((r) => ({ label: r.name, value: r.id }));
  const roleName = (id: number | null) => roles.find((r) => r.id === id)?.name ?? '—';

  const openCreate = () => {
    setEditing(null);
    setModalOpen(true);
  };
  const openEdit = (record: UserRow) => {
    setEditing(record);
    setModalOpen(true);
  };

  const handleToggle = (record: UserRow) => {
    modal.confirm({
      title: `确认${record.is_active ? '禁用' : '启用'}用户「${record.user_name}」？`,
      onOk: async () => {
        await toggleUserStatus(record.id);
        message.success('状态已更新');
        actionRef.current?.reload();
      },
    });
  };

  const handleSubmit = async (values: any) => {
    if (editing) {
      // 更新：密码留空则不改
      const body: any = { user_name: values.user_name, role_id: values.role_id, is_active: values.is_active };
      if (values.password) body.password = values.password;
      await updateUser(editing.id, body);
      message.success('用户已更新');
    } else {
      await createUser({
        user_name: values.user_name,
        password: values.password,
        role_id: values.role_id,
        is_active: values.is_active ?? true,
      });
      message.success('用户已创建');
    }
    setModalOpen(false);
    actionRef.current?.reload();
    return true;
  };

  const columns: ProColumns<UserRow>[] = [
    { title: '用户名', dataIndex: 'user_name', ellipsis: true },
    {
      title: '显示名',
      dataIndex: 'display_name',
      search: false,
      ellipsis: true,
      // 本地账号大多没有显示名，用占位符而不是空白——空单元格会被读成「数据没取到」。
      render: (_, r) => r.display_name || <span style={{ color: 'rgba(0,0,0,.25)' }}>—</span>,
    },
    {
      title: '来源',
      dataIndex: 'external_idp',
      search: false,
      width: 90,
      // 两者的可管理性不同：本地账号能改密码，钉钉账号没有密码可改，停用是唯一手段。
      // 不做「已绑定/未绑定」——账号只按 unionId 匹配，从来不存在「绑定」这个动作。
      render: (_, r) =>
        r.external_idp === 'dingtalk' ? (
          <Tag color="cyan">钉钉</Tag>
        ) : (
          <Tag>本地</Tag>
        ),
    },
    {
      title: '角色',
      dataIndex: 'role_id',
      search: false,
      render: (_, r) => <Tag color="blue">{roleName(r.role_id)}</Tag>,
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      search: false,
      render: (_, r) =>
        r.is_active ? <Tag color="green">启用</Tag> : <Tag color="red">禁用</Tag>,
    },
    {
      title: '创建时间',
      dataIndex: 'created_at',
      search: false,
      valueType: 'dateTime',
    },
    {
      title: '操作',
      valueType: 'option',
      key: 'option',
      render: (_, record) => [
        <a key="edit" onClick={() => openEdit(record)}>
          编辑
        </a>,
        <a key="toggle" onClick={() => handleToggle(record)}>
          {record.is_active ? '禁用' : '启用'}
        </a>,
      ],
    },
  ];

  return (
    <PageContainer title="用户管理">
      <ProTable<UserRow>
        actionRef={actionRef}
        rowKey="id"
        columns={columns}
        request={async (params) => {
          const res = await fetchUsers({
            current: params.current,
            pageSize: params.pageSize,
            user_name: params.user_name,
          });
          return { data: res.data, total: res.total, success: res.success };
        }}
        toolBarRender={() => [
          <Button key="add" type="primary" icon={<PlusOutlined />} onClick={openCreate}>
            新建用户
          </Button>,
        ]}
        pagination={{ pageSize: 20 }}
      />

      <ModalForm
        title={editing ? `编辑用户：${editing.user_name}` : '新建用户'}
        open={modalOpen}
        onOpenChange={setModalOpen}
        initialValues={
          editing
            ? { user_name: editing.user_name, role_id: editing.role_id, is_active: editing.is_active }
            : { is_active: true }
        }
        modalProps={{ destroyOnClose: true }}
        onFinish={handleSubmit}
      >
        <ProFormText
          name="user_name"
          label="用户名"
          rules={[{ required: true, message: '请输入用户名' }]}
          disabled={!!editing}
          extra={editing ? '用户名不可修改' : '仅字母和数字，至少 3 位'}
        />
        <ProFormText.Password
          name="password"
          label="密码"
          rules={editing ? [] : [{ required: true, message: '请输入密码' }]}
          extra={editing ? '留空则不修改密码' : '至少 6 位'}
        />
        <ProFormSelect
          name="role_id"
          label="角色"
          options={roleOptions}
          rules={[{ required: true, message: '请选择角色' }]}
        />
        <ProFormSwitch name="is_active" label="启用" />
      </ModalForm>
    </PageContainer>
  );
}
