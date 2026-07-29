import { InboxOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { App, Card, Form, Radio, Select, Space, Table, Upload } from 'antd';
import type { UploadProps } from 'antd';
import { useEffect, useState } from 'react';
import {
  getProcessingStatus,
  getSites,
  type ImportStatusItem,
  type Period,
  type SiteOption,
} from '@/services/upload';
import { ossDirectUploadAndImport } from '@/services/ossUpload';

const { Dragger } = Upload;

// 周期选择：AUTO=按文件名推断（后端从文件名解析 report_date 与 period）
type PeriodChoice = 'AUTO' | Period;

/**
 * 数据上传屏（阶段五）。日报/周报同一入口：选站点 + 周期，拖入 CSV →
 * Upload customRequest 驱动 ali-oss STS 分片直传 → 触发导入 → 轮询 /processing-status。
 */
export default function UploadPage() {
  const { message } = App.useApp();
  const [sites, setSites] = useState<SiteOption[]>([]);
  const [site, setSite] = useState<string>();
  const [period, setPeriod] = useState<PeriodChoice>('AUTO');
  const [statusItems, setStatusItems] = useState<ImportStatusItem[]>([]);

  useEffect(() => {
    getSites().then(setSites).catch(() => undefined);
  }, []);

  // 导入状态轮询（每 5s）
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let alive = true;
    const poll = async () => {
      try {
        const res = await getProcessingStatus();
        if (alive) setStatusItems(res.items ?? []);
      } catch {
        // 轮询失败静默，下次重试
      }
      if (alive) timer = setTimeout(poll, 5000);
    };
    poll();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, []);

  const customRequest: UploadProps['customRequest'] = async (options) => {
    const { file, onProgress, onSuccess, onError } = options;
    if (!site) {
      message.warning('请先选择站点');
      onError?.(new Error('未选择站点'));
      return;
    }
    try {
      await ossDirectUploadAndImport(file as File, {
        site,
        period: period === 'AUTO' ? undefined : period,
        onProgress: (percent) => onProgress?.({ percent }),
      });
      onSuccess?.({}, file as any);
      message.success(`${(file as File).name} 已直传并触发导入`);
    } catch (e: any) {
      onError?.(e);
      message.error(`上传失败：${e?.message || e}`);
    }
  };

  return (
    <PageContainer title="数据上传">
      <Space direction="vertical" size="large" style={{ width: '100%' }}>
        <Card>
          <Form layout="inline" style={{ marginBottom: 16 }}>
            <Form.Item label="站点" required>
              <Select
                style={{ width: 200 }}
                placeholder="选择站点"
                value={site}
                onChange={setSite}
                options={sites.map((s) => ({ label: `${s.name_zh} (${s.code})`, value: s.code }))}
              />
            </Form.Item>
            <Form.Item label="周期">
              <Radio.Group
                value={period}
                onChange={(e) => setPeriod(e.target.value)}
                optionType="button"
              >
                <Radio.Button value="AUTO">按文件名推断</Radio.Button>
                <Radio.Button value="DAY">日报</Radio.Button>
                <Radio.Button value="WEEK">周报</Radio.Button>
              </Radio.Group>
            </Form.Item>
          </Form>

          <Dragger multiple accept=".csv" disabled={!site} customRequest={customRequest}>
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 CSV 文件到此区域直传</p>
            <p className="ant-upload-hint">
              GB 级文件经 STS 凭证直传 OSS，不经应用服务器；文件名须含报告日期（…_2026_07_18.csv）
            </p>
          </Dragger>
        </Card>

        <Card title="最近导入状态" size="small">
          <Table<ImportStatusItem>
            rowKey="batch_name"
            size="small"
            pagination={false}
            dataSource={statusItems}
            columns={[
              { title: '批次', dataIndex: 'batch_name' },
              {
                title: '进度',
                dataIndex: 'progress_percent',
                render: (v: number) => `${v}%`,
              },
              { title: '总记录', dataIndex: 'total_records' },
              { title: '状态', dataIndex: 'status' },
            ]}
          />
        </Card>
      </Space>
    </PageContainer>
  );
}
