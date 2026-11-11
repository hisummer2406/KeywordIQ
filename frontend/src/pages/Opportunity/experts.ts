/**
 * 机会中心的专家（change add-opportunity-ip-agent 决策 14）。
 *
 * 前端常量，**不为它新增端点**：没有数据源支撑的端点只会把"没做"伪装成"查了但没有"
 * （opportunity-center 规格）。原来的阶段 Tab 都改成专家；只有 IP 专家已接入，其余四位
 * 标「未接入」，写明依赖的 issue 与接入后能做什么，不能发起任务，也不发任何请求。
 */

export type ExpertKey = 'ip' | 'signal' | 'selection' | 'validation' | 'trademark';

export interface Expert {
  key: ExpertKey;
  name: string;
  /** 一句话角色 */
  title: string;
  /** 负责原工作流的哪个阶段 */
  stage: string;
  description: string;
  skills: string[];
  /** 用到的数据源与工具 */
  sources: string[];
  available: boolean;
  /** 未接入时依赖的 issue */
  dependsOn?: string[];
  color: string;
  initial: string;
}

export const EXPERTS: Expert[] = [
  {
    key: 'ip',
    name: 'IP 专家',
    title: '外观专利风险调研',
    stage: '风险核查',
    description:
      '根据产品名称和特征写检索式，经你确认后在智慧芽检索外观专利、取证，再基于证据写风险分析，并回答你的追问。最终结论由你按市场签署。',
    skills: ['生成检索方案', '检索与取证（需确认）', '风险分析', '对话追问', '定时复检'],
    sources: ['智慧芽专利数据库 MCP', '大模型（OpenAI 兼容）'],
    available: true,
    color: '#1677ff',
    initial: 'IP',
  },
  {
    key: 'signal',
    name: '信号洞察专家',
    title: '发现排名与份额异动',
    stage: '待处理信号',
    description: '按确定性规则发现关键词排名、点击份额与转化份额的异常波动，推送给你判断是否值得跟进。',
    skills: ['波动排行', '规则化信号', '事件推送'],
    sources: ['ABA 日报 / 周报'],
    available: false,
    dependsOn: ['#7', '#54'],
    color: '#fa8c16',
    initial: '信',
  },
  {
    key: 'selection',
    name: '选品机会专家',
    title: '把信号整理成候选机会',
    stage: '候选机会',
    description: '承接值得跟进的信号，补齐关键词、ASIN 与类目信息，说明触发原因，形成候选机会。',
    skills: ['候选机会整理', 'ASIN 反查', '指标口径对齐'],
    sources: ['ABA 数据', 'ASIN 反查'],
    available: false,
    dependsOn: ['#55', '#38'],
    color: '#52c41a',
    initial: '选',
  },
  {
    key: 'validation',
    name: '验证跟踪专家',
    title: '跟踪机会的验证进度',
    stage: '验证中 / 已通过 / 已否决',
    description: '跟踪正在验证的机会，提醒节点、汇总反馈，记录通过或否决的决策。',
    skills: ['验证进度', '提醒', '决策记录'],
    sources: ['机会档案（待建）'],
    available: false,
    dependsOn: ['#13'],
    color: '#722ed1',
    initial: '验',
  },
  {
    key: 'trademark',
    name: '商标风险专家',
    title: '商标侵权风险核查',
    stage: '风险核查',
    description: '检索目标市场的近似商标，提示品牌名与关键词的商标风险。',
    skills: ['商标检索', '近似判断提示'],
    sources: ['商标数据源（待定）'],
    available: false,
    dependsOn: ['#25'],
    color: '#eb2f96',
    initial: '标',
  },
];

export const EXPERT_BY_KEY = Object.fromEntries(EXPERTS.map((e) => [e.key, e])) as Record<ExpertKey, Expert>;
