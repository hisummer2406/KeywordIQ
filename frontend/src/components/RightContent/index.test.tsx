import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Modal } from 'antd';

const { push, installState } = vi.hoisted(() => ({
  push: vi.fn(),
  // 安装入口的状态由 hook 决定；判定逻辑在 utils/pwaInstall.test.ts 里测，这里只测入口的呈现
  installState: { mode: 'hidden' as 'hidden' | 'prompt' | 'safari-guide', install: vi.fn() },
}));
vi.mock('@umijs/max', () => ({ history: { push } }));
vi.mock('@/hooks/useInstallEntry', () => ({
  useInstallEntry: () => ({ mode: installState.mode, install: installState.install }),
}));

import RightContent from '@/components/RightContent';

function user(overrides: Partial<API.CurrentUser> = {}): API.CurrentUser {
  return {
    id: 1,
    username: 'alice',
    role: 'operator',
    role_name: '运营',
    permissions: ['analysis:view', 'analysis:export', 'upload:import'],
    is_active: true,
    is_super: false,
    ...overrides,
  } as API.CurrentUser;
}

/** 展开头像下拉。菜单是懒渲染的，不点开读不到菜单项。 */
async function openMenu(currentUser: API.CurrentUser) {
  render(<RightContent currentUser={currentUser} onLogout={vi.fn()} />);
  await act(async () => {
    await userEvent.click(screen.getByText(currentUser.username));
  });
}

/**
 * 用户管理是**不可授予**的能力：判据只有 is_super，与后端 `require_super()` 同源。
 *
 * 这组用例守的是入口的显隐。要强调的是隐藏入口**不是**访问控制——真正拦住的是路由
 * access 守卫和端点依赖；这里只保证不给用户一个点了必然 403 的入口。
 */
describe('顶栏账号区', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    installState.mode = 'hidden';
  });

  // Safari 指引用的是静态 Modal.info，挂在测试渲染树之外，cleanup 不会替它收拾
  afterEach(() => Modal.destroyAll());

  it('超级账号能看到用户管理入口，点击后跳转 /user', async () => {
    await openMenu(user({ is_super: true, role: 'admin', role_name: '超级管理员' }));

    const entry = screen.getByText('用户管理');
    await act(async () => {
      await userEvent.click(entry);
    });

    expect(push).toHaveBeenCalledWith('/user');
  });

  it('普通用户看不到用户管理入口', async () => {
    await openMenu(user());
    expect(screen.queryByText('用户管理')).toBeNull();
  });

  it('admin 角色但非超级账号，同样看不到——判据是 is_super 不是角色码', async () => {
    // 与后端 require_super 的核心回归对应：把角色码算进判据，就等于让「谁能管用户」
    // 变成改角色配置就能改变的事
    await openMenu(user({ role: 'admin', role_name: '超级管理员', is_super: false }));
    expect(screen.queryByText('用户管理')).toBeNull();
  });

  it('未登录时整块不渲染', () => {
    const { container } = render(<RightContent currentUser={undefined} onLogout={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });

  /**
   * SSO 账号的 username 是钉钉 userid（一串数字），不是人看的名字。
   * display_name 存在时必须优先展示它，username 只是没有 display_name 时的退路
   * （本地账号的常态）。
   */
  it('有 display_name 时优先展示它，而不是 username', async () => {
    render(<RightContent currentUser={user({ display_name: '张三' })} onLogout={vi.fn()} />);

    expect(screen.getByText('张三')).toBeInTheDocument();
    expect(screen.queryByText('alice')).not.toBeInTheDocument();
  });

  it('没有 display_name 时退回 username', async () => {
    render(<RightContent currentUser={user({ display_name: null })} onLogout={vi.fn()} />);

    expect(screen.getByText('alice')).toBeInTheDocument();
  });

  it('有 avatar_url 时头像用它渲染成 img', () => {
    const { container } = render(
      <RightContent currentUser={user({ avatar_url: 'https://static.dingtalk.com/a.png' })} onLogout={vi.fn()} />,
    );

    const img = container.querySelector('img');
    expect(img).toHaveAttribute('src', 'https://static.dingtalk.com/a.png');
  });

  it('没有 avatar_url 时不渲染 img，回落到图标', () => {
    const { container } = render(<RightContent currentUser={user()} onLogout={vi.fn()} />);

    expect(container.querySelector('img')).toBeNull();
  });

  it('退出登录始终在，且调用传入的回调', async () => {
    const onLogout = vi.fn();
    render(<RightContent currentUser={user()} onLogout={onLogout} />);
    await act(async () => {
      await userEvent.click(screen.getByText('alice'));
    });
    await act(async () => {
      await userEvent.click(screen.getByText('退出登录'));
    });
    expect(onLogout).toHaveBeenCalled();
  });

  /**
   * 安装为网站应用的入口（issue #92）。显隐的判据（独立窗口、Safari 版本等）在
   * pwaInstall.test.ts 里测；这里守的是「入口据 hook 给出的状态呈现」以及点击后各走各的路。
   */
  describe('安装入口', () => {
    it('hidden：不出现——独立窗口与不支持的环境不显示无效入口', async () => {
      installState.mode = 'hidden';
      await openMenu(user());
      expect(screen.queryByText('安装海眸系统')).toBeNull();
    });

    it('prompt：点击调用浏览器的安装确认，且不弹指引', async () => {
      installState.mode = 'prompt';
      await openMenu(user());
      await act(async () => {
        await userEvent.click(screen.getByText('安装海眸系统'));
      });
      expect(installState.install).toHaveBeenCalledTimes(1);
      expect(screen.queryByText(/添加到程序坞…/)).toBeNull();
    });

    it('safari-guide：点击给出「添加到程序坞」指引，不调用安装事件', async () => {
      installState.mode = 'safari-guide';
      await openMenu(user());
      await act(async () => {
        await userEvent.click(screen.getByText('安装海眸系统'));
      });
      expect(await screen.findByText(/文件 → 添加到程序坞…/)).toBeInTheDocument();
      expect(installState.install).not.toHaveBeenCalled();
    });

    it('与用户管理并存时都在，退出登录仍在最后', async () => {
      installState.mode = 'prompt';
      await openMenu(user({ is_super: true, role: 'admin', role_name: '超级管理员' }));
      const labels = screen.getAllByRole('menuitem').map((el) => el.textContent);
      expect(labels).toEqual(['用户管理', '安装海眸系统', '退出登录']);
    });
  });
});
