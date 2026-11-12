import { Alert, Avatar, Button, Descriptions, Space, Tag, theme, Typography } from 'antd';
import type { ReactNode } from 'react';
import { DECISION, dt, RISK_LEVEL, TASK_STATUS } from '@/pages/IP/riskLabels';
import type { IpMeta, ResearchDetail } from '@/services/opportunityIp';
import { EXPERT_BY_KEY } from '../experts';
import AnalysisCard from './AnalysisCard';
import Rows from './Rows';
import { QUERY_STATUS, queryResultText, TASK_KIND, taskErrorText } from './ipLabels';
import type { ResultsSection } from './ResultsDrawer';
import type { TimelineItem } from './timeline';

/**
 * 对话消息流里的每种消息（决策 10）。供应商与模型的文本一律作为纯文本渲染。
 */

const IP = EXPERT_BY_KEY.ip;

export function ExpertAvatar({ size = 32 }: { size?: number }) {
  return (
    <Avatar size={size} style={{ background: IP.color, flex: 'none', fontSize: size * 0.4 }}>
      {IP.initial}
    </Avatar>
  );
}

export function UserAvatar({ size = 32 }: { size?: number }) {
  const { token } = theme.useToken();
  return (
    <Avatar size={size} style={{ flex: 'none', background: token.colorTextQuaternary }}>
      我
    </Avatar>
  );
}

/** 一条消息放进 Bubble.List 时的角色与头部信息；气泡本身由 X 的 Bubble 提供。 */
export interface BubbleMeta {
  role: 'ai' | 'user' | 'system';
  title?: ReactNode;
  at?: string | null;
  /** 宽卡（检索结果、分析）占满可用宽度 */
  wide?: boolean;
}

export function bubbleMeta(item: TimelineItem): BubbleMeta {
  switch (item.kind) {
    case 'brief':
      return { role: 'user', at: item.at, title: '任务简报' };
    case 'plan':
      return { role: 'ai', at: item.at, title: `检索方案 · ${item.plan.model}` };
    case 'search':
      return {
        role: 'ai',
        at: item.at,
        wide: true,
        title: (
          <Space size={4}>
            执行检索
            {item.task.scheduled && <Tag color="purple">定时复检</Tag>}
            <Tag color={TASK_STATUS[item.task.status].color}>
              {TASK_STATUS[item.task.status].text}
            </Tag>
          </Space>
        ),
      };
    case 'detail':
      return {
        role: 'ai',
        at: item.at,
        title: (
          <Space size={4}>
            取证
            <Tag color={TASK_STATUS[item.task.status].color}>
              {TASK_STATUS[item.task.status].text}
            </Tag>
          </Space>
        ),
      };
    case 'analysis':
      return { role: 'ai', at: item.at, wide: true, title: `风险分析 · ${item.analysis.model}` };
    case 'message':
      return item.message.role === 'user'
        ? { role: 'user', at: item.message.created_at }
        : {
            role: 'ai',
            at: item.message.created_at,
            title: item.message.model ? `回答 · ${item.message.model}` : '回答',
          };
    default:
      return { role: 'system' };
  }
}

/** 气泡头部：说话人、标题、时间（沿用原先自写气泡的文字层级） */
export function BubbleHeader({ meta }: { meta: BubbleMeta }) {
  const { token } = theme.useToken();
  if (meta.role === 'system') return null;
  return (
    <div style={{ fontSize: 12, color: token.colorTextTertiary }}>
      {meta.role === 'user' ? '我' : IP.name}
      {meta.title && <span style={{ marginLeft: 8 }}>{meta.title}</span>}
      {meta.at && <span style={{ marginLeft: 8 }}>{dt(meta.at)}</span>}
    </div>
  );
}

interface ItemProps {
  item: TimelineItem;
  data: ResearchDetail;
  meta: IpMeta;
  busy: boolean;
  isLatestPlan: boolean;
  onUsePlan: (queries: string[]) => void;
  openResults: (s: ResultsSection) => void;
  openSchedule: () => void;
}

export default function MessageBody({
  item,
  data,
  meta,
  busy,
  isLatestPlan,
  onUsePlan,
  openResults,
  openSchedule,
}: ItemProps) {
  const marketName = new Map(meta.markets.map((m) => [m.code, m.name]));
  const pn = new Map(data.candidates.map((c) => [c.id, c.pn ?? c.patent_id]));

  switch (item.kind) {
    case 'brief':
      return (
        <>
          <Typography.Text strong>{data.product_name}</Typography.Text>
          {data.description && (
            <Typography.Paragraph style={{ margin: '4px 0 8px', whiteSpace: 'pre-wrap' }}>
              {data.description}
            </Typography.Paragraph>
          )}
          <Descriptions size="small" column={1} colon={false} styles={{ label: { width: 72 } }}>
            <Descriptions.Item label="销售市场">
              {data.markets.map((m) => marketName.get(m) ?? m).join('、')}
            </Descriptions.Item>
            <Descriptions.Item label="检索法域">
              {data.jurisdiction_details.map((j) => `${j.name}（${j.code}）`).join('、')}
            </Descriptions.Item>
            {data.keywords.length > 0 && (
              <Descriptions.Item label="关键词">{data.keywords.join('、')}</Descriptions.Item>
            )}
            {data.loc_classes.length > 0 && (
              <Descriptions.Item label="洛迦诺">{data.loc_classes.join('、')}</Descriptions.Item>
            )}
          </Descriptions>
        </>
      );

    case 'plan': {
      const { content } = item.plan;
      return (
        <>
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            我按从窄到宽写了 {content.queries.length} 条检索式。确认或修改后才会调用智慧芽。
          </Typography.Paragraph>
          <Rows bordered>
            {content.queries.map((q) => (
              <Space key={q.query_text} orientation="vertical" size={0}>
                <Typography.Text code copyable>
                  {q.query_text}
                </Typography.Text>
                {q.rationale && <Typography.Text type="secondary">{q.rationale}</Typography.Text>}
              </Space>
            ))}
          </Rows>
          {content.assumptions.length > 0 && (
            <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
              {`我的假设：${content.assumptions.join('；')}`}
            </Typography.Paragraph>
          )}
          <Space style={{ marginTop: 10 }}>
            <Button
              type={isLatestPlan ? 'primary' : 'default'}
              onClick={() => onUsePlan(content.queries.map((q) => q.query_text))}
              disabled={busy}
            >
              确认并编辑检索式
            </Button>
          </Space>
        </>
      );
    }

    case 'search': {
      const { queries, newCandidates } = item;
      const ok = queries.some((q) => q.status === 'success');
      return (
        <>
          <Rows>
            {queries.map((q) => (
              <Space key={q.id} orientation="vertical" size={2} style={{ width: '100%' }}>
                <Typography.Text code>{q.query_text}</Typography.Text>
                <Space size={4} wrap>
                  <Tag color={QUERY_STATUS[q.status].color}>{QUERY_STATUS[q.status].text}</Tag>
                  <Typography.Text type={q.status === 'success' ? undefined : 'danger'}>
                    {queryResultText(q)}
                  </Typography.Text>
                </Space>
              </Space>
            ))}
          </Rows>
          <Typography.Paragraph style={{ margin: '8px 0' }}>
            {ok
              ? newCandidates
                ? `本次新增 ${newCandidates} 件候选（已按专利去重）。`
                : '本次没有新增候选：命中的专利此前已在候选里，或检索范围内未找到。'
              : '这次检索没有成功的结果，候选没有变化。'}
          </Typography.Paragraph>
          <Space wrap>
            <Button onClick={() => openResults('candidates')}>查看候选并取证</Button>
            {ok && (
              <Button onClick={openSchedule}>
                {data.schedule ? '修改定时复检' : '设为定时复检'}
              </Button>
            )}
          </Space>
        </>
      );
    }

    case 'detail':
      return (
        <>
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            {`本次计划调用智慧芽 ${item.task.planned_mcp_calls} 次。`}
            {item.task.error_category && taskErrorText('detail', item.task.error_category)}
          </Typography.Paragraph>
          <Button onClick={() => openResults('candidates')}>查看证据</Button>
        </>
      );

    case 'analysis':
      return (
        <>
          <AnalysisCard data={data} analysis={item.analysis} />
          <Button style={{ marginTop: 10 }} onClick={() => openResults('reviews')}>
            去签署人工意见
          </Button>
        </>
      );

    case 'message': {
      const m = item.message;
      if (m.role === 'user') {
        return <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{m.content}</Typography.Text>;
      }
      return (
        <>
          <Typography.Paragraph style={{ marginBottom: 6, whiteSpace: 'pre-wrap' }}>
            {m.content}
          </Typography.Paragraph>
          {m.cited_candidate_ids.length > 0 && (
            <Space size={4} wrap style={{ marginBottom: 6 }}>
              <Typography.Text type="secondary">引用：</Typography.Text>
              {m.cited_candidate_ids.map((id) => (
                <Tag key={id}>{pn.get(id) ?? `#${id}`}</Tag>
              ))}
            </Space>
          )}
          <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block' }}>
            Agent 回复，仅供参考，不构成法律意见。
          </Typography.Text>
        </>
      );
    }

    case 'review': {
      const r = item.review;
      return (
        <Alert
          type="success"
          showIcon
          title={
            <Space size={4} wrap>
              {`${r.signer_name ?? '人工'} 签署了 ${marketName.get(r.market) ?? r.market} 的人工意见：`}
              <Tag
                color={RISK_LEVEL[r.risk_level].color}
              >{`风险 ${RISK_LEVEL[r.risk_level].text}`}</Tag>
              <Tag color={DECISION[r.decision].color}>{DECISION[r.decision].text}</Tag>
              <Typography.Text type="secondary">{dt(r.created_at)}</Typography.Text>
            </Space>
          }
          description={<span style={{ whiteSpace: 'pre-wrap' }}>{r.basis}</span>}
        />
      );
    }

    case 'task-failed':
      return (
        <Alert
          type={item.task.status === 'unknown' ? 'warning' : 'error'}
          showIcon
          title={`${TASK_KIND[item.task.kind]}：${TASK_STATUS[item.task.status].text}${item.task.kind === 'ask' ? '，回答未生成' : ''}`}
          description={taskErrorText(item.task.kind, item.task.error_category) || undefined}
        />
      );

    case 'schedule-skipped':
      return (
        <Alert
          type="warning"
          showIcon
          title={`定时复检已跳过（${dt(item.at)}）`}
          description={item.reason}
        />
      );

    default:
      return null;
  }
}
