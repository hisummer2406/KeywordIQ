import { Alert, Avatar, Button, Descriptions, List, Space, Tag, theme, Typography } from 'antd';
import type { ReactNode } from 'react';
import { DECISION, dt, RISK_LEVEL, TASK_STATUS } from '@/pages/IP/riskLabels';
import type { IpMeta, ResearchDetail } from '@/services/opportunityIp';
import { EXPERT_BY_KEY } from '../experts';
import AnalysisCard from './AnalysisCard';
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

interface BubbleProps {
  side: 'agent' | 'user' | 'system';
  title?: ReactNode;
  at?: string | null;
  children: ReactNode;
  wide?: boolean;
}

export function Bubble({ side, title, at, children, wide }: BubbleProps) {
  const { token } = theme.useToken();
  if (side === 'system') {
    return <div style={{ margin: '4px 48px' }}>{children}</div>;
  }
  const user = side === 'user';
  return (
    <div style={{ display: 'flex', gap: 12, flexDirection: user ? 'row-reverse' : 'row', alignItems: 'flex-start' }}>
      {user ? (
        <Avatar size={32} style={{ flex: 'none', background: token.colorTextQuaternary }}>
          我
        </Avatar>
      ) : (
        <ExpertAvatar />
      )}
      <div style={{ maxWidth: wide ? 'min(880px, 100%)' : 'min(640px, 85%)', minWidth: 0, flex: wide ? 1 : undefined }}>
        <div style={{ fontSize: 12, color: token.colorTextTertiary, marginBottom: 4, textAlign: user ? 'right' : 'left' }}>
          {user ? '我' : IP.name}
          {title && <span style={{ marginLeft: 8 }}>{title}</span>}
          {at && <span style={{ marginLeft: 8 }}>{dt(at)}</span>}
        </div>
        <div
          style={{
            background: user ? token.colorPrimaryBg : token.colorBgContainer,
            border: `1px solid ${user ? token.colorPrimaryBorder : token.colorBorderSecondary}`,
            borderRadius: 12,
            padding: '10px 14px',
          }}
        >
          {children}
        </div>
      </div>
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

export default function MessageItem({ item, data, meta, busy, isLatestPlan, onUsePlan, openResults, openSchedule }: ItemProps) {
  const marketName = new Map(meta.markets.map((m) => [m.code, m.name]));
  const pn = new Map(data.candidates.map((c) => [c.id, c.pn ?? c.patent_id]));

  switch (item.kind) {
    case 'brief':
      return (
        <Bubble side="user" at={item.at} title="任务简报">
          <Typography.Text strong>{data.product_name}</Typography.Text>
          {data.description && (
            <Typography.Paragraph style={{ margin: '4px 0 8px', whiteSpace: 'pre-wrap' }}>{data.description}</Typography.Paragraph>
          )}
          <Descriptions size="small" column={1} colon={false} labelStyle={{ width: 72 }}>
            <Descriptions.Item label="销售市场">{data.markets.map((m) => marketName.get(m) ?? m).join('、')}</Descriptions.Item>
            <Descriptions.Item label="检索法域">
              {data.jurisdiction_details.map((j) => `${j.name}（${j.code}）`).join('、')}
            </Descriptions.Item>
            {data.keywords.length > 0 && <Descriptions.Item label="关键词">{data.keywords.join('、')}</Descriptions.Item>}
            {data.loc_classes.length > 0 && <Descriptions.Item label="洛迦诺">{data.loc_classes.join('、')}</Descriptions.Item>}
          </Descriptions>
        </Bubble>
      );

    case 'plan': {
      const { content, model } = item.plan;
      return (
        <Bubble side="agent" at={item.at} title={`检索方案 · ${model}`}>
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            我按从窄到宽写了 {content.queries.length} 条检索式。确认或修改后才会调用智慧芽。
          </Typography.Paragraph>
          <List
            size="small"
            bordered
            dataSource={content.queries}
            renderItem={(q) => (
              <List.Item>
                <Space direction="vertical" size={0}>
                  <Typography.Text code copyable>
                    {q.query_text}
                  </Typography.Text>
                  {q.rationale && <Typography.Text type="secondary">{q.rationale}</Typography.Text>}
                </Space>
              </List.Item>
            )}
          />
          {content.assumptions.length > 0 && (
            <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
              {`我的假设：${content.assumptions.join('；')}`}
            </Typography.Paragraph>
          )}
          <Space style={{ marginTop: 10 }}>
            <Button type={isLatestPlan ? 'primary' : 'default'} onClick={() => onUsePlan(content.queries.map((q) => q.query_text))} disabled={busy}>
              确认并编辑检索式
            </Button>
          </Space>
        </Bubble>
      );
    }

    case 'search': {
      const { task, queries, newCandidates } = item;
      const ok = queries.some((q) => q.status === 'success');
      return (
        <Bubble
          side="agent"
          at={item.at}
          wide
          title={
            <Space size={4}>
              执行检索
              {task.scheduled && <Tag color="purple">定时复检</Tag>}
              <Tag color={TASK_STATUS[task.status].color}>{TASK_STATUS[task.status].text}</Tag>
            </Space>
          }
        >
          <List
            size="small"
            dataSource={queries}
            renderItem={(q) => (
              <List.Item style={{ paddingInline: 0 }}>
                <Space direction="vertical" size={2} style={{ width: '100%' }}>
                  <Typography.Text code>{q.query_text}</Typography.Text>
                  <Space size={4} wrap>
                    <Tag color={QUERY_STATUS[q.status].color}>{QUERY_STATUS[q.status].text}</Tag>
                    <Typography.Text type={q.status === 'success' ? undefined : 'danger'}>{queryResultText(q)}</Typography.Text>
                  </Space>
                </Space>
              </List.Item>
            )}
          />
          <Typography.Paragraph style={{ margin: '8px 0' }}>
            {ok
              ? newCandidates
                ? `本次新增 ${newCandidates} 件候选（已按专利去重）。`
                : '本次没有新增候选：命中的专利此前已在候选里，或检索范围内未找到。'
              : '这次检索没有成功的结果，候选没有变化。'}
          </Typography.Paragraph>
          <Space wrap>
            <Button onClick={() => openResults('candidates')}>查看候选并取证</Button>
            {ok && <Button onClick={openSchedule}>{data.schedule ? '修改定时复检' : '设为定时复检'}</Button>}
          </Space>
        </Bubble>
      );
    }

    case 'detail':
      return (
        <Bubble
          side="agent"
          at={item.at}
          title={
            <Space size={4}>
              取证
              <Tag color={TASK_STATUS[item.task.status].color}>{TASK_STATUS[item.task.status].text}</Tag>
            </Space>
          }
        >
          <Typography.Paragraph style={{ marginBottom: 8 }}>
            {`本次计划调用智慧芽 ${item.task.planned_mcp_calls} 次。`}
            {item.task.error_category && taskErrorText('detail', item.task.error_category)}
          </Typography.Paragraph>
          <Button onClick={() => openResults('candidates')}>查看证据</Button>
        </Bubble>
      );

    case 'analysis':
      return (
        <Bubble side="agent" at={item.at} wide title={`风险分析 · ${item.analysis.model}`}>
          <AnalysisCard data={data} analysis={item.analysis} />
          <Button style={{ marginTop: 10 }} onClick={() => openResults('reviews')}>
            去签署人工意见
          </Button>
        </Bubble>
      );

    case 'message': {
      const m = item.message;
      if (m.role === 'user') {
        return (
          <Bubble side="user" at={m.created_at}>
            <Typography.Text style={{ whiteSpace: 'pre-wrap' }}>{m.content}</Typography.Text>
          </Bubble>
        );
      }
      return (
        <Bubble side="agent" at={m.created_at} title={m.model ? `回答 · ${m.model}` : '回答'}>
          <Typography.Paragraph style={{ marginBottom: 6, whiteSpace: 'pre-wrap' }}>{m.content}</Typography.Paragraph>
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
        </Bubble>
      );
    }

    case 'review': {
      const r = item.review;
      return (
        <Bubble side="system">
          <Alert
            type="success"
            showIcon
            message={
              <Space size={4} wrap>
                {`${r.signer_name ?? '人工'} 签署了 ${marketName.get(r.market) ?? r.market} 的人工意见：`}
                <Tag color={RISK_LEVEL[r.risk_level].color}>{`风险 ${RISK_LEVEL[r.risk_level].text}`}</Tag>
                <Tag color={DECISION[r.decision].color}>{DECISION[r.decision].text}</Tag>
                <Typography.Text type="secondary">{dt(r.created_at)}</Typography.Text>
              </Space>
            }
            description={<span style={{ whiteSpace: 'pre-wrap' }}>{r.basis}</span>}
          />
        </Bubble>
      );
    }

    case 'task-failed':
      return (
        <Bubble side="system">
          <Alert
            type={item.task.status === 'unknown' ? 'warning' : 'error'}
            showIcon
            message={`${TASK_KIND[item.task.kind]}：${TASK_STATUS[item.task.status].text}${item.task.kind === 'ask' ? '，回答未生成' : ''}`}
            description={taskErrorText(item.task.kind, item.task.error_category) || undefined}
          />
        </Bubble>
      );

    case 'schedule-skipped':
      return (
        <Bubble side="system">
          <Alert type="warning" showIcon message={`定时复检已跳过（${dt(item.at)}）`} description={item.reason} />
        </Bubble>
      );

    default:
      return null;
  }
}
