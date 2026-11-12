import { Alert, Collapse, List, Space, Tag, Typography } from 'antd';
import type { StoreBatchAddResponse } from '@/services/competitor';

/**
 * 批量加入的结果，三段分开呈现。
 *
 * **失败段必须逐条列出原因，不能只报条数。** 「5 条失败」等于让用户自己去猜是哪几条、
 * 为什么——而这几类原因对应的动作完全不同：非 Shopify 是根本不受支持（换个站点），
 * 打不开可能是临时的（稍后重试），域名不合法是他粘错了（改一改再来）。
 *
 * 每一条回指用户粘的**原文那一行**而不是规范化后的域名，否则他无法把结果与自己粘的
 * 内容对上。
 */
export default function BatchAddResult({ result }: { result: StoreBatchAddResponse }) {
  const { added, already_watched: existing, rejected } = result;

  return (
    <Space orientation="vertical" style={{ width: '100%' }} size={12}>
      <Space size={8} wrap>
        <Tag color="green">已加入 {added.length}</Tag>
        {existing.length > 0 && <Tag>已关注过 {existing.length}</Tag>}
        {rejected.length > 0 && <Tag color="red">未能加入 {rejected.length}</Tag>}
      </Space>

      {added.length === 0 && rejected.length > 0 && (
        <Alert
          type="warning"
          showIcon
          title="这一批都没能加入"
          description="下面逐条列出了原因。「不是 Shopify」是根本不受支持，换个站点；「无法访问」可能是临时的，稍后再试。"
        />
      )}

      {rejected.length > 0 && (
        <Collapse
          defaultActiveKey={['rejected']}
          items={[
            {
              key: 'rejected',
              label: `未能加入的 ${rejected.length} 条`,
              children: (
                <List
                  size="small"
                  dataSource={rejected}
                  renderItem={(item) => (
                    <List.Item>
                      <Space orientation="vertical" size={0} style={{ width: '100%' }}>
                        <Typography.Text ellipsis={{ tooltip: item.input }}>
                          {item.input}
                        </Typography.Text>
                        <Typography.Text type="danger" style={{ fontSize: 12 }}>
                          {item.reason}
                        </Typography.Text>
                      </Space>
                    </List.Item>
                  )}
                />
              ),
            },
          ]}
        />
      )}

      {added.length > 0 && (
        <Collapse
          items={[
            {
              key: 'added',
              label: `已加入的 ${added.length} 条`,
              children: (
                <List
                  size="small"
                  dataSource={added}
                  renderItem={(item) => (
                    <List.Item>
                      <Space size={8}>
                        <Typography.Text strong>{item.name || item.domain}</Typography.Text>
                        <Typography.Text type="secondary">{item.domain}</Typography.Text>
                        {item.currency && <Tag>{item.currency}</Tag>}
                      </Space>
                    </List.Item>
                  )}
                />
              ),
            },
          ]}
        />
      )}

      {existing.length > 0 && (
        <Collapse
          items={[
            {
              key: 'existing',
              label: `本来就已关注的 ${existing.length} 条`,
              children: (
                <List
                  size="small"
                  dataSource={existing}
                  renderItem={(item) => <List.Item>{item.domain}</List.Item>}
                />
              ),
            },
          ]}
        />
      )}
    </Space>
  );
}
