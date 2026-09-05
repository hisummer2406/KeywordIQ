import { PageContainer } from '@ant-design/pro-components';
import { Card, Empty } from 'antd';

export default function Page() {
  return (
    <PageContainer title="风险案例">
      <Card>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="风险案例库尚未接入。后续将在这里检索历史案例、查看证据与处理记录。"
        />
      </Card>
    </PageContainer>
  );
}
