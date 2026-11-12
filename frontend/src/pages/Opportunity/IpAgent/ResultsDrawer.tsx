import { Drawer, Radio, Space, Table, Tag, Typography } from 'antd';
import ReviewPanel from '@/pages/IP/RecordDetail/ReviewPanel';
import { dt, TASK_STATUS } from '@/pages/IP/riskLabels';
import { addIpReview, type IpMeta, type IpTask, type ResearchDetail } from '@/services/opportunityIp';
import { CoverageFacts } from './AnalysisCard';
import CandidatePanel from './CandidatePanel';
import { TASK_KIND, taskErrorText } from './ipLabels';

/**
 * 任务「成果」：候选与取证、人工意见（按市场签署）、任务与调用记录（决策 10）。
 * 人工意见与 Agent 分析分开：这里只有人填写的意见，Agent 不写、不预填。
 */

export type ResultsSection = 'candidates' | 'reviews' | 'tasks';

interface Props {
  open: boolean;
  section: ResultsSection;
  onSection: (s: ResultsSection) => void;
  onClose: () => void;
  data: ResearchDetail;
  meta: IpMeta;
  busy: boolean;
  onChanged: () => void;
}

export default function ResultsDrawer({ open, section, onSection, onClose, data, meta, busy, onChanged }: Props) {
  const marketName = new Map(meta.markets.map((m) => [m.code, m.name]));
  const evidenceOptions = data.candidates.map((c) => ({ value: c.id, label: c.pn ?? c.patent_id }));
  const reviewed = data.markets.filter((m) => (data.reviews[m] ?? []).length > 0).length;

  return (
    <Drawer open={open} onClose={onClose} size={1000} title={`成果 · ${data.product_name}`} destroyOnHidden={false}>
      <Space orientation="vertical" style={{ width: '100%' }} size={16}>
        <Radio.Group
          optionType="button"
          buttonStyle="solid"
          value={section}
          onChange={(e) => onSection(e.target.value)}
          options={[
            { value: 'candidates', label: `候选与取证（${data.candidates.length}）` },
            { value: 'reviews', label: `人工意见（${reviewed}/${data.markets.length}）` },
            { value: 'tasks', label: `任务记录（${data.tasks.length}）` },
          ]}
        />
        {section === 'candidates' && (
          <>
            <CoverageFacts c={data.coverage} />
            <CandidatePanel data={data} tools={meta.detail_tools} maxBatch={meta.max_detail_batch} busy={busy} onQueued={onChanged} />
          </>
        )}
        {section === 'reviews' && (
          <Space orientation="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              人工意见按销售市场分别签署，只追加不修改。Agent 的分析与回答不会写进这里。
            </Typography.Text>
            {data.markets.map((m) => (
              <ReviewPanel
                key={m}
                checkId={data.id}
                market={m}
                marketName={marketName.get(m) ?? m}
                reviews={data.reviews[m] ?? []}
                candidates={[]}
                evidenceOptions={evidenceOptions}
                searchCompleted={data.search_done}
                canReview
                submit={(body) => addIpReview(data.id, body)}
                onSaved={onChanged}
              />
            ))}
          </Space>
        )}
        {section === 'tasks' && (
          <Space orientation="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              {data.calls.length
                ? data.calls.map((c) => `${c.provider === 'mcp' ? 'MCP' : '大模型'} ${c.tool} ${c.outcome} ×${c.count}`).join('；')
                : '还没有外部调用'}
              {data.calls.length > 0 && '（计费状态：未知）'}
            </Typography.Text>
            <Table<IpTask>
              rowKey="id"
              size="small"
              pagination={false}
              dataSource={data.tasks}
              locale={{ emptyText: '还没有任务' }}
              columns={[
                {
                  title: '任务',
                  render: (_: unknown, t) => (
                    <Space size={4}>
                      {TASK_KIND[t.kind]}
                      {t.scheduled && <Tag color="purple">定时复检</Tag>}
                    </Space>
                  ),
                },
                {
                  title: '状态',
                  render: (_: unknown, t) => (
                    <Space size={4}>
                      <Tag color={TASK_STATUS[t.status].color}>{TASK_STATUS[t.status].text}</Tag>
                      {t.error_category && <Typography.Text type="secondary">{taskErrorText(t.kind, t.error_category)}</Typography.Text>}
                    </Space>
                  ),
                },
                { title: '计划 MCP 调用', dataIndex: 'planned_mcp_calls', width: 120 },
                { title: '创建', dataIndex: 'created_at', width: 150, render: (v: string) => dt(v) },
                { title: '完成', dataIndex: 'finished_at', width: 150, render: (v: string | null) => dt(v) },
              ]}
            />
          </Space>
        )}
      </Space>
    </Drawer>
  );
}
