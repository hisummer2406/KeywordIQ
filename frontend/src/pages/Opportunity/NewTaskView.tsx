import { Tooltip, Typography, theme } from 'antd';
import type { IpMeta, Workspace } from '@/services/opportunityIp';
import NewTaskComposer from './IpAgent/NewTaskComposer';
import { ExpertBadge } from './ExpertsView';
import { EXPERTS } from './experts';

/**
 * 新建任务（机会中心首页）：选专家，再把需求发给他。目前只有 IP 专家可选；未接入的专家只展示、
 * 不可选、不发请求（opportunity-center 规格）。
 */

interface Props {
  meta: IpMeta | null;
  workspaces: Workspace[];
  defaultWorkspaceId: number | null;
  onCreated: (id: number) => void;
}

export default function NewTaskView({ meta, workspaces, defaultWorkspaceId, onCreated }: Props) {
  const { token } = theme.useToken();
  return (
    <div style={{ padding: '48px 24px', overflowY: 'auto', height: '100%' }}>
      <div style={{ maxWidth: 860, margin: '0 auto' }}>
        <Typography.Title level={3} style={{ textAlign: 'center', marginBottom: 8 }}>
          今天想让哪位专家帮你？
        </Typography.Title>
        <Typography.Paragraph type="secondary" style={{ textAlign: 'center', marginBottom: 28 }}>
          专家给建议和证据，会花钱的调用都先问你；结论由你签署。
        </Typography.Paragraph>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, justifyContent: 'center', marginBottom: 20 }}>
          {EXPERTS.map((e) => {
            const chip = (
              <div
                key={e.key}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: 8,
                  padding: '6px 14px 6px 6px',
                  borderRadius: 999,
                  border: `1px solid ${e.available ? e.color : token.colorBorderSecondary}`,
                  background: e.available ? token.colorPrimaryBg : token.colorFillQuaternary,
                  color: e.available ? token.colorText : token.colorTextQuaternary,
                  cursor: e.available ? 'default' : 'not-allowed',
                }}
              >
                <ExpertBadge expert={e} size={28} />
                <span>{e.name}</span>
              </div>
            );
            return e.available ? (
              chip
            ) : (
              <Tooltip key={e.key} title={`尚未接入，依赖 ${e.dependsOn?.join('、')}`}>
                {chip}
              </Tooltip>
            );
          })}
        </div>
        {meta ? (
          <NewTaskComposer key={defaultWorkspaceId ?? 'none'} meta={meta} workspaces={workspaces} defaultWorkspaceId={defaultWorkspaceId} onCreated={onCreated} />
        ) : (
          <Typography.Text type="secondary">正在加载 IP 专家配置…</Typography.Text>
        )}
        {meta && !meta.mcp_configured && (
          <Typography.Paragraph type="warning" style={{ marginTop: 12 }}>
            智慧芽接口未配置，任务可以创建，但检索与取证不可用，请联系管理员。
          </Typography.Paragraph>
        )}
      </div>
    </div>
  );
}
