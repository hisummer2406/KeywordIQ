import { Avatar, Button, Card, Space, Tag, Typography, theme } from 'antd';
import { type Expert, EXPERTS } from './experts';

/**
 * 专家名片（change add-opportunity-ip-agent 决策 14）。名片内容是前端常量，打开这一屏不发请求；
 * 未接入的专家只说明依赖与接入后的能力，不能发起任务。
 */

export function ExpertBadge({ expert, size = 44 }: { expert: Expert; size?: number }) {
  return (
    <Avatar size={size} style={{ background: expert.available ? expert.color : undefined, flex: 'none', fontSize: size * 0.36 }}>
      {expert.initial}
    </Avatar>
  );
}

export default function ExpertsView({ onStart }: { onStart: () => void }) {
  const { token } = theme.useToken();
  return (
    <div style={{ padding: 24, overflowY: 'auto', height: '100%' }}>
      <div style={{ maxWidth: 1080, margin: '0 auto' }}>
        <Typography.Title level={4} style={{ marginTop: 0 }}>
          专家
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          每位专家负责选品工作流里的一个阶段。专家给出的是建议和证据，结论由你确认；未接入的专家会在依赖的能力上线后开放。
        </Typography.Paragraph>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
          {EXPERTS.map((e) => (
            <Card key={e.key} style={{ opacity: e.available ? 1 : 0.85, borderRadius: 12 }} styles={{ body: { padding: 20 } }}>
              <Space align="start" size={12}>
                <ExpertBadge expert={e} />
                <div>
                  <Space size={6} wrap>
                    <Typography.Text strong style={{ fontSize: 16 }}>
                      {e.name}
                    </Typography.Text>
                    {e.available ? <Tag color="success">可用</Tag> : <Tag>未接入</Tag>}
                  </Space>
                  <div style={{ color: token.colorTextSecondary }}>{e.title}</div>
                </div>
              </Space>
              <Typography.Paragraph style={{ margin: '12px 0 8px', minHeight: 66 }}>{e.description}</Typography.Paragraph>
              <div style={{ fontSize: 12, color: token.colorTextTertiary, marginBottom: 4 }}>{`负责阶段：${e.stage}`}</div>
              <Space size={[4, 4]} wrap style={{ marginBottom: 8 }}>
                {e.skills.map((s) => (
                  <Tag key={s} bordered={false}>
                    {s}
                  </Tag>
                ))}
              </Space>
              <div style={{ fontSize: 12, color: token.colorTextTertiary, marginBottom: 12 }}>{`数据与工具：${e.sources.join('、')}`}</div>
              {e.available ? (
                <Button type="primary" block onClick={onStart}>
                  发起任务
                </Button>
              ) : (
                <Button block disabled>
                  {`尚未接入 · 依赖 ${e.dependsOn?.join('、')}`}
                </Button>
              )}
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
