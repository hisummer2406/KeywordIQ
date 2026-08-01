import { InboxOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { Alert, App, Card, Descriptions, Form, Radio, Select, Space, Table, Upload } from 'antd';
import type { UploadProps } from 'antd';
import { useEffect, useState } from 'react';
import {
  getProcessingStatus,
  getSites,
  inspectFilename,
  type FilenameMeta,
  type ImportStatusItem,
  type Period,
  type SiteOption,
} from '@/services/upload';
import { ossDirectUploadAndImport } from '@/services/ossUpload';

const { Dragger } = Upload;

// 周期选择：AUTO=按文件名推断（后端从文件名解析 report_date 与 period）
type PeriodChoice = 'AUTO' | Period;

const PERIOD_LABEL: Record<string, string> = { DAY: '日报', WEEK: '周报' };
const NEED_LABEL: Record<string, string> = { site: '站点', period: '周期', report_date: '报告日期' };

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
  const [inspected, setInspected] = useState<FilenameMeta>();

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

  /**
   * 选中文件后先解析文件名，把归属三元组摆到用户眼前，并挡住判定不出又没人指定的情况。
   *
   * 归属决定数据写进哪张事实表的哪个分区，从前它藏在"用户选的站点 + 后端的兜底推断"里，
   * 上传前看不见：选错站点要等后端 400 才知道，而周期判定不出会被静默当成周报。现在改成
   * 上传前就把结论显示出来，判定不出的项要求显式指定，绝不猜。
   */
  const beforeUpload: UploadProps['beforeUpload'] = async (file) => {
    let meta: FilenameMeta;
    try {
      meta = await inspectFilename(file.name);
    } catch {
      // 解析失败（如非 CSV）已由全局 errorHandler 提示，这里只负责不放行
      return Upload.LIST_IGNORE;
    }
    setInspected(meta);

    if (meta.needs.includes('site') && !site) {
      message.warning('文件名里没有站点前缀，请先选择站点');
      return Upload.LIST_IGNORE;
    }
    if (meta.needs.includes('period') && period === 'AUTO') {
      message.warning('文件名判断不出是日报还是周报，请先选择周期');
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const customRequest: UploadProps['customRequest'] = async (options) => {
    const { file, onProgress, onSuccess, onError } = options;
    // 站点以文件名判定为准，判定不出才用界面选的
    const effectiveSite = inspected?.site || site;
    if (!effectiveSite) {
      message.warning('请先选择站点');
      onError?.(new Error('未选择站点'));
      return;
    }
    try {
      await ossDirectUploadAndImport(file as File, {
        site: effectiveSite,
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
            <Form.Item label="站点" tooltip="通常无需选择：站点按文件名前缀判定，判定不出时才需要指定">
              <Select
                allowClear
                style={{ width: 220 }}
                placeholder="按文件名判定"
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
                <Radio.Button value="AUTO">按文件名判定</Radio.Button>
                <Radio.Button value="DAY">日报</Radio.Button>
                <Radio.Button value="WEEK">周报</Radio.Button>
              </Radio.Group>
            </Form.Item>
          </Form>

          {/* 归属回显：上传前就让用户看见这份文件会落到哪一期 */}
          {inspected && (
            <Alert
              type={inspected.needs.length ? 'warning' : 'info'}
              showIcon
              style={{ marginBottom: 16 }}
              message={
                inspected.needs.length
                  ? `文件名判定不出：${inspected.needs
                      .map((n) => NEED_LABEL[n])
                      .join('、')}，请在上方指定`
                  : '按文件名判定的归属'
              }
              description={
                <Descriptions size="small" column={3} style={{ marginTop: 4 }}>
                  <Descriptions.Item label="站点">
                    {inspected.site ?? site ?? '待指定'}
                  </Descriptions.Item>
                  <Descriptions.Item label="周期">
                    {PERIOD_LABEL[inspected.period ?? ''] ?? PERIOD_LABEL[period] ?? '待指定'}
                  </Descriptions.Item>
                  <Descriptions.Item label="报告日期">
                    {inspected.report_date ?? '待指定'}
                  </Descriptions.Item>
                </Descriptions>
              }
            />
          )}

          <Dragger multiple accept=".csv" beforeUpload={beforeUpload} customRequest={customRequest}>
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
