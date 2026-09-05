import { PageContainer } from '@ant-design/pro-components';
import { Card, Empty, Tabs } from 'antd';

const sections = [
  {
    key: 'calculate',
    label: '快速核价',
    description: '核价功能尚未接入。后续支持目标售价、预计利润和采购价上限测算。',
  },
  {
    key: 'records',
    label: '核价记录',
    description: '核价记录尚未接入。后续支持保存方案、查看历史依据与重新核价。',
  },
  {
    key: 'rates',
    label: '费用配置',
    description: '费用配置尚未接入。后续支持物流报价、平台费用与汇率版本管理。',
  },
];

export default function PricingPage() {
  return (
    <PageContainer title="定价核算">
      <Tabs
        items={sections.map(({ key, label, description }) => ({
          key,
          label,
          children: (
            <Card>
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />
            </Card>
          ),
        }))}
      />
    </PageContainer>
  );
}
