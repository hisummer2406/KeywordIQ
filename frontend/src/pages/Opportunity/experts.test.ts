import { describe, expect, it } from 'vitest';
import { EXPERTS, TASK_EXPERTS } from './experts';

describe('专家名片（spec opportunity-leads「专家名片反映真实接入状态」）', () => {
  it('已接入的专家都有入口与按钮文案', () => {
    const available = EXPERTS.filter((e) => e.available);
    expect(available.map((e) => [e.key, e.entry])).toEqual([
      ['signal', 'leads'],
      ['research', 'dossiers'],
      ['ip', 'task'],
    ]);
    expect(available.every((e) => !!e.entryLabel)).toBe(true);
  });

  it('未接入的专家没有入口，并写明还缺什么', () => {
    const blocked = EXPERTS.filter((e) => !e.available);
    expect(blocked.map((e) => e.key)).toEqual(['feasibility', 'trademark']);
    expect(blocked.every((e) => e.entry === undefined && !!e.blockedBy)).toBe(true);
  });

  it('新建任务页只列以任务对话方式工作的专家', () => {
    expect(TASK_EXPERTS.map((e) => e.key)).toEqual(['ip']);
  });
});
