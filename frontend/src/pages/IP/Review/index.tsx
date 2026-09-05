import { PageContainer } from '@ant-design/pro-components';
import { Card, Empty } from 'antd';

export default function Page() {
  return (
    <PageContainer title="审核待办">
      <Card>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="审核流程尚未接入。后续将在这里处理产品核查、补充证据与复审事项。"
        />
      </Card>
    </PageContainer>
  );
}
