import { PageContainer } from '@ant-design/pro-components';
import { Alert, Card, Empty, Tabs, Typography } from 'antd';

/**
 * 机会中心：导航与权限骨架（issue #76），不含任何业务逻辑。
 *
 * 六个状态对应"确定性 Signal → 候选机会 → 验证推进 → 风险核查"这条主动式工作流，
 * 但信号计算、机会评分、验证流程都在别的 issue 里（见每个 Tab 的说明文案）。**这一屏
 * 现在不发任何请求**——没有数据源支撑的端点只会把"没做"伪装成"查了但没有"，比不做
 * 更容易误导人（design.md 决策 2/3）。
 *
 * 六个状态目前的差异只是文案，没有做成六个独立组件：真实的结构差异（比如"验证中"要
 * 显示进度、"已通过"要显示决策记录）要等业务能力接入后才会出现，届时再拆有真实依据。
 */
const STAGES: { key: string; label: string; description: string }[] = [
  {
    key: 'pending-signal',
    label: '待处理信号',
    description:
      '确定性关键词 Signal 与波动排行（issue #7）、主动式事件推送（issue #54）尚未接入。' +
      '接入后，这里会列出系统按规则发现的排名/份额异常，等待人工判断是否值得跟进。',
  },
  {
    key: 'candidate',
    label: '候选机会',
    description:
      'PRD 范围、数据源优先级与指标口径尚在收口（issue #55），ASIN 反查（issue #38）' +
      '也未接入。这里会承接被判定为"值得跟进"的信号，附带关键词/ASIN/类目等对象信息与' +
      '触发原因。',
  },
  {
    key: 'verifying',
    label: '验证中',
    description:
      '多租户、提醒与效果验证（issue #13）尚未接入。这里会显示正在跟踪验证的机会，' +
      '及其验证进度与已有反馈。',
  },
  {
    key: 'risk-review',
    label: '风险待核查',
    description:
      '商标与外观专利数据源（issue #24 / #25）尚未接入——与"风险核查"菜单同一个缺口。' +
      '接入前这里不会出现任何风险提示，也不构成法律意见。',
  },
  {
    key: 'approved',
    label: '已通过',
    description: '验证流程（issue #13）尚未接入，因此还没有"已通过"的机会可以显示。',
  },
  {
    key: 'archived',
    label: '已否决 / 已归档',
    description: '验证流程（issue #13）尚未接入，因此还没有被否决或归档的机会可以显示。',
  },
];

function StagePlaceholder({ description }: { description: string }) {
  return (
    <Card>
      <Empty
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={<Typography.Text type="secondary">该阶段尚未接入，暂无可显示的机会</Typography.Text>}
      >
        <Typography.Paragraph type="secondary" style={{ maxWidth: 560, margin: '0 auto' }}>
          {description}
        </Typography.Paragraph>
      </Empty>
    </Card>
  );
}

export default function OpportunityPage() {
  return (
    <PageContainer title="机会中心">
      <Alert
        style={{ marginBottom: 16 }}
        type="info"
        showIcon
        message="导航与权限骨架"
        description="机会中心目前只搭好了入口与权限边界，信号计算、机会评分、验证与风险核查等能力会按依赖的 issue 逐步接入；接入前不发起任何后端请求，也不展示编造的数值或结论。"
      />
      <Tabs
        items={STAGES.map((stage) => ({
          key: stage.key,
          label: stage.label,
          children: <StagePlaceholder description={stage.description} />,
        }))}
      />
    </PageContainer>
  );
}
