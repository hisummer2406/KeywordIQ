import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess, useSearchParams } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Form,
  Image,
  Input,
  Radio,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { UploadFile } from 'antd/es/upload/interface';
import { useEffect, useMemo, useRef, useState } from 'react';
import { createCheck, getCheck, getRiskMeta, newToken, type RiskMeta } from '@/services/risk';

/**
 * 外观专利核查表单（specs/patent-risk-check）。
 *
 * - 首屏和输入变化**不发起任何检索**；只有点「提交检索」才创建任务（P010 上传 + P060 检索）。
 * - 检索法域与销售市场分开：推荐只是提示，必须由用户勾选（点「勾选推荐」也是用户动作）。
 * - 防重复提交：同一组输入复用同一个令牌，双击只建一单；改了任何输入就换新令牌。
 * - `?from=<id>` 基于上一轮发起新一轮：带入原输入，图片需重新选择，意见不带入。
 */

interface FormValues {
  product_name: string;
  product_version: string;
  source_ref?: string;
  description?: string;
  markets: string[];
  jurisdictions: string[];
  page_size: number;
}

const ACCEPT = ['image/jpeg', 'image/png'];

export default function DesignPatentPage() {
  const access = useAccess();
  const [params] = useSearchParams();
  const fromId = Number(params.get('from')) || null;
  const [form] = Form.useForm<FormValues>();
  const [meta, setMeta] = useState<RiskMeta | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | undefined>();
  const [submitting, setSubmitting] = useState(false);
  const [previous, setPrevious] = useState<{ id: number; label: string } | null>(null);
  const token = useRef(newToken());

  const markets = Form.useWatch('markets', form) ?? [];
  const jurisdictions = Form.useWatch('jurisdictions', form) ?? [];

  useEffect(() => {
    getRiskMeta()
      .then((m) => {
        setMeta(m);
        form.setFieldsValue({ page_size: m.default_page_size });
      })
      .catch(() => undefined);
  }, [form]);

  useEffect(() => {
    if (!fromId) return;
    getCheck(fromId)
      .then((c) => {
        form.setFieldsValue({
          product_name: c.product_name,
          product_version: c.product_version,
          source_ref: c.source_ref ?? undefined,
          description: c.description ?? undefined,
          markets: c.markets,
          jurisdictions: c.jurisdictions,
          page_size: c.page_size,
        });
        setPrevious({ id: c.id, label: `${c.product_name} · ${c.product_version}` });
      })
      .catch(() => setPrevious(null));
  }, [fromId, form]);

  useEffect(() => () => {
    if (preview) URL.revokeObjectURL(preview);
  }, [preview]);

  const recommended = useMemo(() => {
    if (!meta) return [];
    const set = new Set<string>();
    markets.forEach((m) => (meta.recommendations[m] ?? []).forEach((j) => set.add(j)));
    return [...set].filter((j) => !jurisdictions.includes(j));
  }, [meta, markets, jurisdictions]);

  const jMeta = useMemo(() => new Map((meta?.jurisdictions ?? []).map((j) => [j.code, j])), [meta]);
  const notes = jurisdictions.map((j) => jMeta.get(j)).filter((j) => j && (j.note || !j.verified));

  const resetToken = () => {
    token.current = newToken();
  };

  const pickFile = (f: File) => {
    if (!ACCEPT.includes(f.type)) {
      message.error('只支持 JPG 或 PNG 图片');
      return Upload.LIST_IGNORE;
    }
    if (meta && f.size > meta.max_image_bytes) {
      message.error('图片不能超过 4 MB');
      return Upload.LIST_IGNORE;
    }
    setFile(f);
    setPreview(URL.createObjectURL(f));
    resetToken();
    return false;
  };

  const onSubmit = async (v: FormValues) => {
    if (!file) {
      message.error('请先选择一张产品原图');
      return;
    }
    setSubmitting(true);
    try {
      const r = await createCheck({
        ...v,
        previous_check_id: previous?.id ?? null,
        client_token: token.current,
        image: file,
      });
      message.success('已提交检索，结果会在核查详情中更新');
      resetToken();
      history.push(`/ip/review/${r.id}`);
    } finally {
      setSubmitting(false);
    }
  };

  if (!meta) {
    return (
      <PageContainer title="外观专利">
        <Card>
          <Spin />
        </Card>
      </PageContainer>
    );
  }

  const canSubmit = meta.provider_configured && access.canSearchRisk;
  const fileList: UploadFile[] = file ? [{ uid: '1', name: file.name, status: 'done' }] : [];

  return (
    <PageContainer title="外观专利" content="上传产品原图，在所选法域检索相似外观专利，结果与人工意见在「审核待办」中留存。">
      <Space direction="vertical" size="middle" style={{ width: '100%' }}>
        {!meta.provider_configured && (
          <Alert type="warning" showIcon message="智慧芽接口未配置，暂时无法检索。请联系管理员在服务端配置 API Key。" />
        )}
        {meta.provider_configured && !access.canSearchRisk && (
          <Alert type="info" showIcon message="你没有发起专利检索的权限（检索会计费），请联系管理员授予。" />
        )}
        {previous && (
          <Alert
            type="info"
            showIcon
            message={
              <span>
                基于上一轮核查「{previous.label}」发起新一轮：已带入原输入，请重新选择图片。上一轮的意见只适用于上一轮。
              </span>
            }
          />
        )}
        <Card>
          <Form<FormValues>
            form={form}
            layout="vertical"
            onFinish={onSubmit}
            onValuesChange={resetToken}
            disabled={!canSubmit}
          >
            <Row gutter={24}>
              <Col xs={24} md={12}>
                <Form.Item name="product_name" label="产品名称" rules={[{ required: true, whitespace: true, max: 200 }]}>
                  <Input placeholder="例如：便携保温杯" />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item
                  name="product_version"
                  label="产品版本"
                  rules={[{ required: true, whitespace: true, max: 100 }]}
                  extra="换图、改款都算新版本，需要新建一轮核查"
                >
                  <Input placeholder="例如：v2 磨砂款" />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="source_ref" label="来源链接 / ASIN 备注" rules={[{ max: 500 }]}>
                  <Input placeholder="可选" />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="page_size" label="首次读取条数" extra="之后可按需读取下一页（每页一次检索调用）">
                  <Radio.Group options={meta.page_size_options.map((n) => ({ label: `${n} 条`, value: n }))} />
                </Form.Item>
              </Col>
              <Col span={24}>
                <Form.Item name="description" label="产品说明" rules={[{ max: 5000 }]}>
                  <Input.TextArea rows={2} placeholder="可选：外形特点、材质等，便于审核时对照" />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item name="markets" label="销售市场" rules={[{ required: true, message: '至少选择一个销售市场' }]}>
                  <Checkbox.Group options={meta.markets.map((m) => ({ label: `${m.name}（${m.code}）`, value: m.code }))} />
                </Form.Item>
              </Col>
              <Col xs={24} md={12}>
                <Form.Item
                  name="jurisdictions"
                  label="检索法域"
                  rules={[{ required: true, message: '至少选择一个检索法域' }]}
                  extra={
                    recommended.length > 0 ? (
                      <Space size={4} wrap>
                        <span>按销售市场推荐：</span>
                        {recommended.map((j) => (
                          <Tag key={j}>{jMeta.get(j)?.name ?? j}</Tag>
                        ))}
                        <Button
                          size="small"
                          type="link"
                          onClick={() => {
                            form.setFieldValue('jurisdictions', [...jurisdictions, ...recommended]);
                            resetToken();
                          }}
                        >
                          勾选推荐
                        </Button>
                      </Space>
                    ) : null
                  }
                >
                  <Checkbox.Group
                    options={meta.jurisdictions.map((j) => ({
                      label: j.verified ? j.name : `${j.name}（待核实）`,
                      value: j.code,
                    }))}
                  />
                </Form.Item>
              </Col>
            </Row>
            {notes.length > 0 && (
              <Alert
                type="info"
                style={{ marginBottom: 16 }}
                message="检索范围说明"
                description={
                  <ul style={{ margin: 0, paddingLeft: 18 }}>
                    {notes.map((j) => (
                      <li key={j!.code}>
                        {j!.name}：{j!.note}
                        {!j!.verified && '（该法域的供应商取值尚未经真实联调核实）'}
                      </li>
                    ))}
                  </ul>
                }
              />
            )}
            <Form.Item label="产品原图" required extra="一张 JPG 或 PNG，不超过 4 MB。单图核查不等于多视角全面核查，其他视角可另建核查。">
              <Space align="start" size="large">
                <Upload accept=".jpg,.jpeg,.png" maxCount={1} fileList={fileList} beforeUpload={pickFile}
                  onRemove={() => { setFile(null); setPreview(undefined); resetToken(); }}>
                  <Button>选择图片</Button>
                </Upload>
                {preview && <Image src={preview} width={160} alt="产品原图预览" />}
              </Space>
            </Form.Item>
            <Alert
              type="warning"
              style={{ marginBottom: 16 }}
              message="提交后，图片会发送给智慧芽进行检索"
              description="本次将产生两次调用：P010 图片上传、P060 外观图片检索（金额以智慧芽账单为准）。检索结果只用于辅助人工判断，不代表侵权结论。"
            />
            <Button type="primary" htmlType="submit" loading={submitting} disabled={!canSubmit}>
              提交检索
            </Button>
          </Form>
        </Card>
        <Typography.Text type="secondary">
          检索在后台执行，提交后可以离开本页；结果与历史在「审核待办」中查看。
        </Typography.Text>
      </Space>
    </PageContainer>
  );
}
