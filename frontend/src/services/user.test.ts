import { beforeEach, describe, expect, it, vi } from 'vitest';

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock('@umijs/max', () => ({ request: requestMock }));

import { createUser, toggleUserStatus, updateUser } from '@/services/user';

/**
 * 起点缺陷：用户名重复时后端返 HTTP 200 + {status:1,msg}，前端 unwrap 抛的 Error 无人
 * 接管（errorConfig 只处理 HTTP 层错误），页面又没 try/catch——用户什么也看不到。
 *
 * 现在失败是非 2xx，request 直接 reject，错误交给统一出口。这组守住"不再自己解包"。
 */
describe('用户管理接口', () => {
  beforeEach(() => vi.clearAllMocks());

  it('创建成功直接拿到数据，不需要解包信封', async () => {
    requestMock.mockResolvedValue({ id: 7, user_name: 'alice', is_active: true });

    await expect(createUser({ user_name: 'alice', password: 'pwd123' })).resolves.toMatchObject({
      id: 7,
      user_name: 'alice',
    });
  });

  it('失败向上抛，由统一出口提示——不再被解包逻辑吞掉', async () => {
    requestMock.mockRejectedValue(
      Object.assign(new Error('Request failed'), {
        response: { status: 409, data: { code: 'user.duplicate_name', message: '用户名已存在' } },
      }),
    );

    await expect(createUser({ user_name: 'alice', password: 'pwd123' })).rejects.toMatchObject({
      response: { status: 409 },
    });
  });

  it('更新与状态切换同样不解包', async () => {
    requestMock.mockResolvedValue({ id: 7, is_active: false });

    await expect(updateUser(7, { is_active: false })).resolves.toBeTruthy();
    await expect(toggleUserStatus(7)).resolves.toMatchObject({ is_active: false });
    for (const call of requestMock.mock.calls) {
      expect(call[0]).toMatch(/^\/api\/user\//);
    }
  });
});
