import { SendOutlined } from '@ant-design/icons';
import { Button, Collapse, Form, Input, Select, Space, Tag, Typography, theme } from 'antd';
import { useRef, useState } from 'react';
import { createResearch, type IpMeta, requestPlan, type Workspace } from '@/services/opportunityIp';
import { newToken } from '@/services/risk';

/**
 * 向 IP 专家发起任务（决策 10）。发送即创建调研（只保存产品信息）；大模型已配置时随即入队
 * 「生成检索方案」——只调用大模型、不消耗 MCP 预算。检索与取证仍必须在对话卡片里人工确认。
 * 法域推荐只作提示，点一下才加入，不自动勾选（与 #24 一致）。
 */

interface Props {
  meta: IpMeta;
  workspaces: Workspace[];
  defaultWorkspaceId: number | null;
  onCreated: (id: number) => void;
}

interface Values {
  product_name: string;
  description?: string;
  keywords?: string[];
  loc_classes?: string[];
  markets: string[];
  jurisdictions: string[];
  workspace_id: number;
}

export default function NewTaskComposer({ meta, workspaces, defaultWorkspaceId, onCreated }: Props) {
  const { token: t } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const [sending, setSending] = useState(false);
  const token = useRef(newToken());
  const markets = Form.useWatch('markets', form) ?? [];
  const chosen = Form.useWatch('jurisdictions', form) ?? [];
  const recommended = Array.from(new Set(markets.flatMap((m) => meta.recommendations[m] ?? []))).filter(
    (j) => !chosen.includes(j),
  );
  const jName = new Map(meta.jurisdictions.map((j) => [j.code, j.name]));

  const submit = async () => {
    const v = await form.validateFields();
    setSending(true);
    try {
      const r = await createResearch({
        client_token: token.current,
        product_name: v.product_name,
        description: v.description || null,
        keywords: v.keywords ?? [],
        loc_classes: v.loc_classes ?? [],
        markets: v.markets,
        jurisdictions: v.jurisdictions,
        workspace_id: v.workspace_id || null,
      });
      token.current = newToken();
      if (meta.llm_configured) {
        // 方案生成失败不影响任务已创建：错误由全局处理提示，进入对话后可以重试或手写
        await requestPlan(r.id, newToken()).catch(() => undefined);
      }
      form.resetFields();
      onCreated(r.id);
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ border: `1px solid ${t.colorBorder}`, borderRadius: 16, padding: 16, background: t.colorBgContainer }}>
      <Form<Values>
        form={form}
        layout="vertical"
        requiredMark={false}
        initialValues={{ workspace_id: defaultWorkspaceId ?? 0 }}
        onValuesChange={() => {
          token.current = newToken();
        }}
      >
        <Form.Item name="product_name" rules={[{ required: true, whitespace: true, max: 200, message: '写一下产品名称' }]} style={{ marginBottom: 8 }}>
          <Input variant="borderless" placeholder="产品名称，例如：硅胶折叠水杯" style={{ fontSize: 16, fontWeight: 500, paddingInline: 0 }} />
        </Form.Item>
        <Form.Item name="description" rules={[{ max: 5000 }]} style={{ marginBottom: 8 }}>
          <Input.TextArea
            variant="borderless"
            autoSize={{ minRows: 3, maxRows: 10 }}
            style={{ paddingInline: 0 }}
            placeholder="描述产品的外观特征、结构和卖点。IP 专家会据此写检索式，例如：可伸缩折叠的便携硅胶水杯，圆柱形分三节折叠，顶部带翻盖和提绳挂环。"
          />
        </Form.Item>
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
          <Form.Item name="markets" label="销售市场" rules={[{ required: true, message: '至少选择一个市场' }]} style={{ minWidth: 220, flex: 1 }}>
            <Select mode="multiple" placeholder="人工意见按市场签署" options={meta.markets.map((m) => ({ value: m.code, label: `${m.name}（${m.code}）` }))} />
          </Form.Item>
          <Form.Item
            name="jurisdictions"
            label="检索法域"
            rules={[{ required: true, message: '至少选择一个法域' }]}
            style={{ minWidth: 260, flex: 1 }}
            extra={
              recommended.length > 0 && (
                <Space size={4} wrap style={{ marginTop: 4 }}>
                  <Typography.Text type="secondary">按市场建议：</Typography.Text>
                  {recommended.map((j) => (
                    <Tag
                      key={j}
                      style={{ cursor: 'pointer' }}
                      onClick={() => form.setFieldValue('jurisdictions', [...chosen, j])}
                    >
                      {`+ ${jName.get(j) ?? j}`}
                    </Tag>
                  ))}
                </Space>
              )
            }
          >
            <Select mode="multiple" options={meta.jurisdictions.map((j) => ({ value: j.code, label: `${j.name}（${j.code}）` }))} />
          </Form.Item>
          <Form.Item name="workspace_id" label="放进工作空间" style={{ width: 180 }}>
            <Select options={[{ value: 0, label: '未归类' }, ...workspaces.map((w) => ({ value: w.id, label: w.name }))]} />
          </Form.Item>
        </div>
        <Collapse
          ghost
          size="small"
          items={[
            {
              key: 'more',
              label: '更多（关键词、洛迦诺分类）',
              forceRender: true,
              children: (
                <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                  <Form.Item name="keywords" label="关键词" extra="英文关键词命中率最高，回车分隔" style={{ minWidth: 260, flex: 1 }}>
                    <Select mode="tags" tokenSeparators={[',', '，']} placeholder="mug, cup" />
                  </Form.Item>
                  <Form.Item
                    name="loc_classes"
                    label="洛迦诺分类"
                    extra="例如 07-01（餐具）；检索式里加上它才会只命中外观设计"
                    style={{ minWidth: 220, flex: 1 }}
                    rules={[
                      {
                        validator: async (_, v?: string[]) => {
                          if ((v ?? []).some((c) => !/^\d{2}-\d{2}$/.test(c))) throw new Error('格式为 07-01');
                        },
                      },
                    ]}
                  >
                    <Select mode="tags" tokenSeparators={[',', '，', ' ']} placeholder="07-01" />
                  </Form.Item>
                </div>
              ),
            },
          ]}
        />
        <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
          <Typography.Text type="secondary" style={{ flex: 1, fontSize: 12 }}>
            {meta.llm_configured
              ? '发送后 IP 专家先写检索方案（只调用大模型）；检索与取证要你确认后才调用智慧芽。'
              : '大模型未配置：发送后需要你在对话里手写检索式。'}
          </Typography.Text>
          <Button type="primary" icon={<SendOutlined />} loading={sending} onClick={submit}>
            发给 IP 专家
          </Button>
        </div>
      </Form>
    </div>
  );
}
