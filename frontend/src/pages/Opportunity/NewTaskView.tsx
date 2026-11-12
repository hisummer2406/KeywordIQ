import { useAccess } from '@umijs/max';
import { Alert, Typography, theme } from 'antd';
import type { IpMeta, Workspace } from '@/services/opportunityIp';
import NewTaskComposer, { type DossierPreset } from './IpAgent/NewTaskComposer';
import { ExpertBadge } from './ExpertsView';
import { TASK_EXPERTS } from './experts';

/**
 * 向专家发起任务：只列以「任务对话」方式工作的专家（目前是 IP 专家）。信号洞察与选品研究
 * 有各自的视图（线索、机会档案），未接入的专家在专家页说明，不在这里出现。
 */

interface Props {
  meta: IpMeta | null;
  workspaces: Workspace[];
  defaultWorkspaceId: number | null;
  /** 从机会档案发起 IP 调研时带入的内容 */
  preset?: DossierPreset | null;
  onCreated: (id: number) => void;
}

export default function NewTaskView({ meta, workspaces, defaultWorkspaceId, preset, onCreated }: Props) {
  const { token } = theme.useToken();
  const access = useAccess();
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
          {TASK_EXPERTS.map((e) => (
            <div
              key={e.key}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 14px 6px 6px',
                borderRadius: 999,
                border: `1px solid ${e.color}`,
                background: token.colorPrimaryBg,
              }}
            >
              <ExpertBadge expert={e} size={28} />
              <span>{e.name}</span>
            </div>
          ))}
        </div>
        {!access.canSearchRisk && (
          <Alert
            type="info"
            showIcon
            style={{ marginBottom: 12 }}
            title="你可以创建任务、让 IP 专家生成检索方案和回答追问；执行检索与取证会向智慧芽计费，需要「发起专利检索」权限，请联系管理员开通。"
          />
        )}
        {preset && (
          <Typography.Paragraph type="secondary" style={{ textAlign: 'center' }}>
            来自机会档案「{preset.dossierTitle}」：创建后这条调研会挂回该档案，团队能在档案里看到它的结论摘要。请补充产品的外观特征。
          </Typography.Paragraph>
        )}
        {meta ? (
          <NewTaskComposer
            key={`${defaultWorkspaceId ?? 'none'}-${preset?.dossierId ?? 'free'}`}
            meta={meta}
            workspaces={workspaces}
            defaultWorkspaceId={defaultWorkspaceId}
            preset={preset}
            onCreated={onCreated}
          />
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
