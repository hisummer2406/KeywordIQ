import { useSyncExternalStore } from 'react';
import {
  hasInstallPrompt,
  isSafariWithAddToDock,
  isStandalone,
  promptInstall,
  resolveInstallMode,
  subscribeInstallPrompt,
  type InstallMode,
} from '@/utils/pwaInstall';

/**
 * 「安装海眸系统」入口的显隐状态。只读环境、不发请求。
 *
 * 独立窗口与 Safari 版本在页面生命周期内不会变，所以每次渲染现算即可；会变的只有
 * beforeinstallprompt 到没到，那部分走 `useSyncExternalStore` 订阅。
 */
export function useInstallEntry(): { mode: InstallMode; install: typeof promptInstall } {
  const hasPrompt = useSyncExternalStore(subscribeInstallPrompt, hasInstallPrompt, () => false);
  const mode = resolveInstallMode({
    standalone: isStandalone(),
    hasPrompt,
    safariAddToDock: isSafariWithAddToDock(),
  });
  return { mode, install: promptInstall };
}
