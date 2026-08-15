import { PageContainer } from '@ant-design/pro-components';
import { Card, Empty, Typography } from 'antd';

/**
 * 「数据源尚未接入」占位屏。
 *
 * 外观专利与全球商标库缺的不是口径而是**数据本身**——仓库里没有任何专利或商标数据，
 * 导入链路只认 ABA 报表的 21 列。菜单位置先占住，各自另立变更。
 *
 * 两条约束：
 * - **不发任何请求**，也没有对应的后端端点。为一个没有数据的屏建端点，等于把「还没做」
 *   伪装成「做了但查不出来」。
 * - **文案要说明是数据源没接入**，不能只放一个泛泛的空状态——后者读起来像是查询失败
 *   或筛选条件不对，用户会去反复试。
 */
export default function PendingDataSource({
  title,
  description,
}: {
  title: string;
  description: string;
}) {
  return (
    <PageContainer title={title}>
      <Card>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={
            <Typography.Text type="secondary">
              数据源尚未接入，此功能暂不可用。
            </Typography.Text>
          }
        >
          <Typography.Paragraph type="secondary" style={{ maxWidth: 520, margin: '0 auto' }}>
            {description}
          </Typography.Paragraph>
        </Empty>
      </Card>
    </PageContainer>
  );
}
