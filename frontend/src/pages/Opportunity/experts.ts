/**
 * 机会中心的专家（change add-opportunity-ip-agent 决策 14；名片状态见 change
 * add-opportunity-signal-leads 决策 5，spec opportunity-leads「专家名片反映真实接入状态」）。
 *
 * 前端常量，**不为它新增端点**：没有数据源支撑的端点只会把"没做"伪装成"查了但没有"
 * （opportunity-center 规格）。未接入的专家写明还缺什么，不能发起任务，也不发任何请求。
 */

export type ExpertKey = 'ip' | 'signal' | 'research' | 'feasibility' | 'trademark';

/** 已接入的专家点开后去哪：向它发起任务（对话）、线索列表、机会档案 */
export type ExpertEntry = 'task' | 'leads' | 'dossiers';

export interface Expert {
  key: ExpertKey;
  name: string;
  /** 一句话角色 */
  title: string;
  /** 负责工作流的哪个阶段 */
  stage: string;
  description: string;
  skills: string[];
  /** 用到的数据源与工具 */
  sources: string[];
  available: boolean;
  /** 已接入时的入口与按钮文案 */
  entry?: ExpertEntry;
  entryLabel?: string;
  /** 未接入时：还缺什么 */
  blockedBy?: string;
  color: string;
  initial: string;
}

export const EXPERTS: Expert[] = [
  {
    key: 'signal',
    name: '信号洞察专家',
    title: '每天找出持续走强的词',
    stage: '发现线索',
    description:
      '把持续增长榜上的词列成线索，标出哪些已经有人在研究。勾选一个或几个相关的词，可以直接建成机会档案，或并入已有档案。',
    skills: ['持续增长线索', '档案归属标注', '一键建档', '并入档案'],
    sources: ['ABA 日报（持续增长榜，导入时算定）'],
    available: true,
    entry: 'leads',
    entryLabel: '看线索',
    color: '#fa8c16',
    initial: '信',
  },
  {
    key: 'research',
    name: '选品研究专家',
    title: '把想法研究成机会档案',
    stage: '研究、决定与复查',
    description:
      '围绕一个产品想法和一组关键词整理 ABA 证据，列出系统没有的数据并收纳带来源的人工证据；可以就档案里的证据追问。推进、补证、观察还是否决由你签署，到期提醒复查并给出变化。',
    skills: ['ABA 证据快照', '证据缺口', '基于证据追问', '决定与复查'],
    sources: ['ABA 日报 / 周报', '人工证据', '大模型（只读档案内证据）'],
    available: true,
    entry: 'dossiers',
    entryLabel: '打开机会档案',
    color: '#52c41a',
    initial: '研',
  },
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
    entry: 'task',
    entryLabel: '发起任务',
    color: '#1677ff',
    initial: 'IP',
  },
  {
    key: 'feasibility',
    name: '商业可行性专家',
    title: '按成本和费用算能不能赚钱',
    stage: '成本与利润测算',
    description:
      '从机会档案发起核价或关联已有核价单，团队在档案里看到各市场的售价、预计单件利润、利润率、保本价与采购价上限，签决定时一并留档。金额只由核价引擎计算；费用规则未启用的市场只显示原因，不显示金额。',
    skills: ['从档案发起核价', '测算摘要', '决定留档', '追问引用测算'],
    sources: ['定价核算引擎（需已启用的费用规则）'],
    available: true,
    entry: 'dossiers',
    entryLabel: '在机会档案里核价',
    color: '#722ed1',
    initial: '商',
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
    blockedBy: '还没有商标数据源（#25 未定）；目前请把查到的商标信息作为人工证据补进档案',
    color: '#eb2f96',
    initial: '标',
  },
];

export const EXPERT_BY_KEY = Object.fromEntries(EXPERTS.map((e) => [e.key, e])) as Record<ExpertKey, Expert>;

/** 能以「任务对话」方式发起的专家（新建任务页只列这些） */
export const TASK_EXPERTS = EXPERTS.filter((e) => e.entry === 'task');
