import {
  CheckCircleOutlined,
  DownOutlined,
  FileProtectOutlined,
  RightOutlined,
  SearchOutlined,
} from '@ant-design/icons';
import { Alert, Button, Descriptions, Space, Tag, Typography, theme } from 'antd';
import { type ReactNode, useState } from 'react';
import { DECISION, dt, RISK_LEVEL, TASK_STATUS } from '@/pages/IP/riskLabels';
import type { IpMeta, IpQuery, IpTask, ResearchDetail } from '@/services/opportunityIp';
import AnswerMarkdown from '../chat/AnswerMarkdown';
import { CitationTags } from '../chat/roles';
import AnalysisCard from './AnalysisCard';
import Rows from './Rows';
import { QUERY_STATUS, queryResultText, taskErrorText, TASK_KIND } from './ipLabels';
import type { ResultsSection } from './ResultsDrawer';
import type { TimelineItem } from './timeline';

/**
 * 对话消息流里的每种消息（change add-opportunity-ip-agent 决策 10；change refine-opportunity-chat-ui 决策 D4）。
 *
 * 角色：用户输入（简报、追问）→ user；AI 产出（方案、分析、回答）→ ai；任务事件（检索、取证、签署、
 * 失败、跳过）→ event。Agent 回答走受限 Markdown（chat/AnswerMarkdown）；方案、分析与供应商文本仍按纯文本。
 */

/** 一条消息放进 Bubble.List 时的角色、标题与底部元信息 */
export interface BubbleMeta {
  role: 'ai' | 'user' | 'event';
  /** 这是什么；不含模型名与时间 */
  title?: ReactNode;
  /** 底部弱化元信息：提示、模型名、时间 */
  meta?: (string | null | undefined)[];
}

export function bubbleMeta(item: TimelineItem): BubbleMeta {
  switch (item.kind) {
    case 'brief':
      return { role: 'user', title: '任务简报', meta: [dt(item.at)] };
    case 'plan':
      return { role: 'ai', title: '检索方案', meta: [item.plan.model, dt(item.at)] };
    case 'analysis':
      return { role: 'ai', title: '风险分析', meta: [item.analysis.model, dt(item.at)] };
    case 'message':
      return item.message.role === 'user'
        ? { role: 'user', meta: [dt(item.message.created_at)] }
        : {
            role: 'ai',
            title: '回答',
            meta: ['Agent 回复，仅供参考，不构成法律意见', item.message.model, dt(item.message.created_at)],
          };
    default:
      return { role: 'event' };
  }
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

/** 事件行：图标 + 一行摘要 + 链接式动作，可选展开内容 */
function EventLine({
  icon,
  children,
  actions,
  detail,
}: {
  icon: ReactNode;
  children: ReactNode;
  actions?: ReactNode;
  detail?: ReactNode;
}) {
  return (
    <div className="chat-event">
      <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
        <span style={{ flex: 'none', lineHeight: '22px' }}>{icon}</span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', columnGap: 8, rowGap: 2 }}>
            {children}
            {actions}
          </div>
          {detail}
        </div>
      </div>
    </div>
  );
}

const linkBtn = { type: 'link', size: 'small', style: { padding: 0, height: 'auto' } } as const;

function StatusTag({ task }: { task: IpTask }) {
  const s = TASK_STATUS[task.status];
  return (
    <Tag color={s.color} style={{ marginInlineEnd: 0 }}>
      {s.text}
    </Tag>
  );
}

/** 一次执行检索：默认折叠成一行摘要，失败与未知的条数始终可见 */
function SearchEvent({
  task,
  queries,
  newCandidates,
  at,
  hasSchedule,
  openResults,
  openSchedule,
}: {
  task: IpTask;
  queries: IpQuery[];
  newCandidates: number;
  at: string;
  hasSchedule: boolean;
  openResults: (s: ResultsSection) => void;
  openSchedule: () => void;
}) {
  const { token } = theme.useToken();
  const [open, setOpen] = useState(false);
  const count = (s: IpQuery['status']) => queries.filter((q) => q.status === s).length;
  const ok = count('success');
  const failed = count('failed');
  const unknown = count('unknown');
  return (
    <EventLine
      icon={<SearchOutlined />}
      actions={
        <Space size={12} wrap>
          <Button {...linkBtn} icon={open ? <DownOutlined /> : <RightOutlined />} onClick={() => setOpen((v) => !v)}>
            {open ? '收起检索式' : '查看检索式'}
          </Button>
          <Button {...linkBtn} onClick={() => openResults('candidates')}>
            查看候选并取证
          </Button>
          {ok > 0 && (
            <Button {...linkBtn} onClick={openSchedule}>
              {hasSchedule ? '修改定时复检' : '设为定时复检'}
            </Button>
          )}
        </Space>
      }
      detail={
        open && (
          <div style={{ marginTop: 6 }}>
            <Rows bordered>
              {queries.map((q) => (
                <Space key={q.id} orientation="vertical" size={2} style={{ width: '100%' }}>
                  <Typography.Text code style={{ wordBreak: 'break-all' }}>
                    {q.query_text}
                  </Typography.Text>
                  <Space size={4} wrap>
                    <Tag color={QUERY_STATUS[q.status].color}>{QUERY_STATUS[q.status].text}</Tag>
                    <Typography.Text type={q.status === 'success' ? undefined : 'danger'}>
                      {queryResultText(q)}
                    </Typography.Text>
                  </Space>
                </Space>
              ))}
            </Rows>
          </div>
        )
      }
    >
      <Typography.Text strong style={{ fontSize: 13 }}>{`执行检索 ${queries.length} 条`}</Typography.Text>
      {task.scheduled && (
        <Tag color="purple" style={{ marginInlineEnd: 0 }}>
          定时复检
        </Tag>
      )}
      <StatusTag task={task} />
      <span>
        {`成功 ${ok}`}
        {failed > 0 && <span style={{ color: token.colorError }}>{` · 失败 ${failed}`}</span>}
        {unknown > 0 && <span style={{ color: token.colorWarning }}>{` · 结果未知 ${unknown}`}</span>}
        {' · '}
        {ok > 0
          ? newCandidates
            ? `新增 ${newCandidates} 件候选（已按专利去重）`
            : '没有新增候选'
          : '候选没有变化'}
      </span>
      <span className="chat-meta">{dt(at)}</span>
    </EventLine>
  );
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
              <Space key={q.query_text} orientation="vertical" size={0} style={{ width: '100%' }}>
                <Typography.Text code copyable style={{ wordBreak: 'break-all' }}>
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

    case 'search':
      return (
        <SearchEvent
          task={item.task}
          queries={item.queries}
          newCandidates={item.newCandidates}
          at={item.at}
          hasSchedule={!!data.schedule}
          openResults={openResults}
          openSchedule={openSchedule}
        />
      );

    case 'detail': {
      const err = item.task.error_category && taskErrorText('detail', item.task.error_category);
      return (
        <EventLine
          icon={<FileProtectOutlined />}
          actions={
            <Button {...linkBtn} onClick={() => openResults('candidates')}>
              查看证据
            </Button>
          }
        >
          <Typography.Text strong style={{ fontSize: 13 }}>
            取证
          </Typography.Text>
          <StatusTag task={item.task} />
          <span>{`本次计划调用智慧芽 ${item.task.planned_mcp_calls} 次。`}</span>
          {err && <Typography.Text type="danger">{err}</Typography.Text>}
          <span className="chat-meta">{dt(item.at)}</span>
        </EventLine>
      );
    }

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
      if (m.role === 'user') return <>{m.content}</>;
      return (
        <>
          <AnswerMarkdown>{m.content}</AnswerMarkdown>
          {m.cited_candidate_ids.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <CitationTags labels={m.cited_candidate_ids.map((id) => pn.get(id) ?? `#${id}`)} />
            </div>
          )}
        </>
      );
    }

    case 'review': {
      const r = item.review;
      return (
        <EventLine icon={<CheckCircleOutlined style={{ color: 'var(--ant-color-success, #52c41a)' }} />}>
          <span>{`${r.signer_name ?? '人工'} 签署了 ${marketName.get(r.market) ?? r.market} 的人工意见：`}</span>
          <Tag color={RISK_LEVEL[r.risk_level].color} style={{ marginInlineEnd: 0 }}>
            {`风险 ${RISK_LEVEL[r.risk_level].text}`}
          </Tag>
          <Tag color={DECISION[r.decision].color} style={{ marginInlineEnd: 0 }}>
            {DECISION[r.decision].text}
          </Tag>
          <span className="chat-meta">{dt(r.created_at)}</span>
          <div style={{ flexBasis: '100%', whiteSpace: 'pre-wrap', color: 'var(--ant-color-text, inherit)' }}>
            {r.basis}
          </div>
        </EventLine>
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
