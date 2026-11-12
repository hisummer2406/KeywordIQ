import { Alert, Descriptions, Space, Tag, Typography } from 'antd';
import type { AgentOutput, AnalysisContent, Coverage, ResearchDetail } from '@/services/opportunityIp';
import { RELEVANCE, TOOL_TEXT } from './ipLabels';
import Rows from './Rows';

/**
 * Agent 风险分析卡（决策 7）。旁边显示服务端算出的覆盖事实——这些不由模型生成。
 * 分析不是法律意见，也不会写入或预填人工意见。模型文本一律作为纯文本渲染。
 */

export function CoverageFacts({ c }: { c: Coverage }) {
  const gaps = c.queries_failed + c.queries_unknown;
  return (
    <Descriptions size="small" column={3} bordered>
      <Descriptions.Item label="检索式">
        {`${c.queries_success}/${c.queries_total} 条成功`}
        {gaps > 0 && <Tag color="warning" style={{ marginLeft: 6 }}>{`${c.queries_failed} 失败 · ${c.queries_unknown} 未知`}</Tag>}
      </Descriptions.Item>
      <Descriptions.Item label="候选">{c.candidates}</Descriptions.Item>
      <Descriptions.Item label="已取证">
        {(Object.keys(c.detailed) as (keyof Coverage['detailed'])[])
          .map((t) => `${TOOL_TEXT[t]} ${c.detailed[t]}`)
          .join(' · ')}
      </Descriptions.Item>
    </Descriptions>
  );
}

function Bullets({ title, items }: { title: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <Typography.Text strong>{title}</Typography.Text>
      <ul style={{ margin: '4px 0 0', paddingLeft: 20 }}>
        {items.map((t, i) => (
          // eslint-disable-next-line react/no-array-index-key
          <li key={i}>{t}</li>
        ))}
      </ul>
    </div>
  );
}

export default function AnalysisCard({ data, analysis }: { data: ResearchDetail; analysis: AgentOutput<AnalysisContent> }) {
  const pn = new Map(data.candidates.map((c) => [c.id, c.pn ?? c.patent_id]));
  const { content } = analysis;
  return (
    <Space orientation="vertical" style={{ width: '100%' }} size={12}>
      <Alert
        type="info"
        showIcon
        title="Agent 分析，不构成法律意见"
        description="Agent 只看到文字著录信息，看不到产品图片；未命中只代表本次检索范围内没有找到。最终结论请在「成果 → 人工意见」按市场签署。"
      />
      {analysis.coverage && (
        <div>
          <Typography.Text type="secondary">生成时的覆盖（系统统计）</Typography.Text>
          <CoverageFacts c={analysis.coverage} />
        </div>
      )}
      <Typography.Paragraph style={{ marginBottom: 0, whiteSpace: 'pre-wrap' }}>{content.summary}</Typography.Paragraph>
      {content.signals.length > 0 && (
        <Rows bordered>
          {content.signals.map((s) => (
            <Space key={s.candidate_id} align="start">
              <Tag color={RELEVANCE[s.relevance].color}>{RELEVANCE[s.relevance].text}</Tag>
              <Typography.Text strong>{pn.get(s.candidate_id) ?? `#${s.candidate_id}`}</Typography.Text>
              <Typography.Text>{s.reason}</Typography.Text>
            </Space>
          ))}
        </Rows>
      )}
      <Bullets title="覆盖缺口" items={content.coverage_gaps} />
      <Bullets title="无法判断" items={content.unknowns} />
      <Bullets title="建议下一步" items={content.next_steps} />
    </Space>
  );
}
