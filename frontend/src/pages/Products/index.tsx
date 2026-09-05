import { PageContainer } from '@ant-design/pro-components';
import { Card, Empty } from 'antd';

export default function Page() {
  return (
    <PageContainer title="产品档案">
      <Card>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="产品档案功能尚未接入。后续将在这里集中管理产品资料、监测记录，以及关联的风险审核与核价方案。"
        />
      </Card>
    </PageContainer>
  );
}
