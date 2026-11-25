// 设置页的「自动采集（试点）」区块：读写设置、立即运行、停止、最近一轮汇总、导出运行记录
// （delta spec「每轮运行结果可查看与导出」）。页面上没有这个区块时什么都不做。

import { getAutoConfig, isValidDailyTime, isValidPostcode, setAutoConfig } from './auto-config';
import { loadRunHistory, loadRunState, type RunState, type RunStopReason } from './auto-runner';
import type { AutoControlMessage, AutoControlResult } from './messages';

const STOP_REASON_TEXT: Record<RunStopReason, string> = {
  blocked: '遇到验证码或访问拒绝，已停止',
  wrong_delivery: '页面配送位置与目标邮编不符，已停止（请在专用 Chrome Profile 的 Amazon 页头「Deliver to」设成目标邮编后重试）',
  delivery_unconfirmed: '配送位置未确认，已停止（读不到页头配送位置或无法确认是目标邮编，请人工核对后重新运行）',
  page_timeout: '页面超时或无法读取（可能被拦截），已停止',
  auth: '令牌缺失或已过期，已停止',
  config_unavailable: '读不到服务端单批上限，未开始',
  watch_list_unavailable: '读不到关注清单，未开始',
  manual_stop: '已手动停止',
};

const START_FAIL_TEXT: Record<string, string> = {
  already_running: '已有一轮正在运行',
  ...STOP_REASON_TEXT,
};

/** 一轮运行的汇总文字。纯函数，便于测试。 */
export function describeRun(run: RunState | null): string {
  if (!run) return '还没有运行记录。';
  const started = new Date(run.startedAt).toLocaleString();
  const trigger = run.trigger === 'daily' ? '每日定时' : '手动';
  const ok = run.results.filter((r) => r.outcome === 'success').length;
  const limited = run.results.filter((r) => r.outcome === 'success' && r.coverage === 'limited').length;
  const failed = run.results.filter((r) => r.outcome === 'failed').length;
  const stoppedAt = run.results.find((r) => r.outcome === 'stopped');
  const newAsins = run.results.reduce((sum, r) => sum + (r.newAsins ?? 0), 0);
  const lines = [`最近一轮：${started}（${trigger}）`];
  if (run.status === 'running') {
    lines.push(`运行中：第 ${Math.min(run.index + 1, run.queue.length)} / ${run.queue.length} 家`);
  } else {
    lines.push(run.status === 'finished' ? '状态：已完成' : `状态：${STOP_REASON_TEXT[run.stopReason ?? 'manual_stop']}`);
  }
  if (stoppedAt) lines.push(`停在：${stoppedAt.site} 站卖家 ${stoppedAt.sellerId}（这家店未上报）`);
  if ((run.stopReason === 'wrong_delivery' || run.stopReason === 'delivery_unconfirmed') && run.stopDetail) {
    lines.push(`页面显示的配送位置：${run.stopDetail}`);
  }
  lines.push(
    `已处理 ${run.results.length} / ${run.queue.length} 家：有效 ${ok} 家（其中覆盖有限 ${limited} 家），失败 ${failed} 家；新发现合计 ${newAsins} 条`,
  );
  const site = run.deliverySite;
  const target = site ? run.deliveryTargets?.[site] : undefined;
  lines.push(`页面选择器：${run.selectorsVersion ? `服务端下发 ${run.selectorsVersion}` : '内置'}`);
  if (site) lines.push(`配送核对：${site} 站，目标 ${target ?? '未设置'}，实际 ${run.deliveryText ?? '读不到'}`);
  else if (run.deliveryText) lines.push(`采集时的配送地址：${run.deliveryText}`);
  return lines.join('\n');
}

async function send(message: AutoControlMessage): Promise<AutoControlResult> {
  try {
    return ((await chrome.runtime.sendMessage(message)) as AutoControlResult | undefined) ?? { ok: false };
  } catch {
    return { ok: false };
  }
}

export async function initAutoSection(): Promise<void> {
  const enabled = document.getElementById('auto-enabled') as HTMLInputElement | null;
  if (!enabled) return;
  const time = document.getElementById('auto-time') as HTMLInputElement;
  const pages = document.getElementById('auto-pages') as HTMLInputElement;
  const maxStores = document.getElementById('auto-max-stores') as HTMLInputElement;
  const targetUs = document.getElementById('auto-target-us') as HTMLInputElement | null;
  const status = document.getElementById('auto-status') as HTMLElement;
  const summary = document.getElementById('auto-summary') as HTMLElement;

  const config = await getAutoConfig();
  enabled.checked = config.enabled;
  time.value = config.dailyTime;
  pages.value = String(config.pages);
  maxStores.value = String(config.maxStores);
  if (targetUs) targetUs.value = config.deliveryTargets.US ?? '';

  const refreshSummary = async (): Promise<void> => {
    const current = await loadRunState();
    if (current) {
      summary.textContent = describeRun(current);
      return;
    }
    const history = await loadRunHistory();
    summary.textContent = describeRun(history[history.length - 1] ?? null);
  };
  await refreshSummary();

  document.getElementById('auto-save')?.addEventListener('click', () => {
    void (async () => {
      if (!isValidDailyTime(time.value)) {
        status.textContent = '每日时刻格式不对，请用 HH:MM';
        return;
      }
      const deliveryTargets = { ...config.deliveryTargets };
      if (targetUs) {
        const value = targetUs.value.trim();
        if (value && !isValidPostcode('US', value)) {
          status.textContent = '美国站目标邮编须为 5 位数字';
          return;
        }
        if (value) deliveryTargets.US = value;
        else delete deliveryTargets.US;
      }
      const saved = await setAutoConfig({
        enabled: enabled.checked,
        dailyTime: time.value,
        pages: Number(pages.value),
        maxStores: Number(maxStores.value),
        deliveryTargets,
      });
      config.deliveryTargets = saved.deliveryTargets;
      pages.value = String(saved.pages);
      maxStores.value = String(saved.maxStores);
      await send({ type: 'AUTO_CONFIG_CHANGED' });
      status.textContent = saved.enabled ? `已保存，每天 ${saved.dailyTime} 自动运行` : '已保存，自动采集已关闭';
    })();
  });

  document.getElementById('auto-run-now')?.addEventListener('click', () => {
    void (async () => {
      status.textContent = '正在启动…';
      const result = await send({ type: 'AUTO_RUN_NOW' });
      status.textContent = result.ok
        ? '已开始运行，可以关闭本页'
        : `未能开始：${START_FAIL_TEXT[result.reason ?? ''] ?? '插件内部通信异常'}`;
      await refreshSummary();
    })();
  });

  document.getElementById('auto-stop')?.addEventListener('click', () => {
    void (async () => {
      const result = await send({ type: 'AUTO_STOP' });
      status.textContent = result.ok ? '已停止' : '当前没有正在运行的一轮';
      await refreshSummary();
    })();
  });

  document.getElementById('auto-export')?.addEventListener('click', () => {
    void (async () => {
      const history = await loadRunHistory();
      const current = await loadRunState();
      const runs = current?.status === 'running' ? [...history, current] : history;
      const blob = new Blob([JSON.stringify(runs, null, 2)], { type: 'application/json' });
      const link = document.createElement('a');
      link.href = URL.createObjectURL(blob);
      link.download = `keywordiq-auto-runs-${new Date().toISOString().slice(0, 10)}.json`;
      link.click();
      URL.revokeObjectURL(link.href);
    })();
  });

  chrome.storage.onChanged?.addListener(() => {
    void refreshSummary();
  });
}
