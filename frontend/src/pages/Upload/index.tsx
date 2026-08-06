import { InboxOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { Alert, App, Card, Space, Table, Typography, Upload } from 'antd';
import type { UploadProps } from 'antd';
import { useEffect, useRef, useState } from 'react';
import {
  getProcessingStatus,
  inspectFilename,
  type FilenameMeta,
  type ImportStatusItem,
} from '@/services/upload';
import { ossDirectUploadAndImport } from '@/services/ossUpload';

const { Dragger } = Upload;
const { Text } = Typography;

const PERIOD_LABEL: Record<string, string> = { DAY: '日报', WEEK: '周报' };

/**
 * 归属回显里的一项。标签与值挨在一起，整组左对齐。
 *
 * 这里刻意不用 Descriptions：它是等分栅格，column={3} 会把「US / 日报 / 2026-08-07」
 * 这三个短值平摊到整个卡片宽度上（约 1360px），值与值之间隔着半屏空白，读的时候要横扫
 * 一整行才能把三项凑齐。三个字段本就该是一行里连读的一句话，不是表格。
 */
function ScopeField({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <Space size={6}>
      <Text type="secondary">{label}</Text>
      <Text strong>{value}</Text>
    </Space>
  );
}

/** 判定不出时告诉用户该怎么改文件名——界面不提供绕过文件名的途径，提示必须可操作。 */
const NEED_HINT: Record<string, string> = {
  site: '站点：文件名需以已登记的站点码开头，如 DE_…',
  period: '周期：文件名需含日报或周报标识，如 …_Day_… / …_Week_…',
  report_date: '报告日期：文件名需含日期，如 …_2026_07_18.csv',
};

/**
 * 数据上传屏。
 *
 * 归属三元组（站点 / 周期 / 报告日期）**完全由文件名判定**，界面不提供站点与周期的选择项：
 * 报告日期本就只看文件名，站点在前缀里，周期在关键词里，用户再选一遍只是多一次选错的机会
 * ——实测中确实出现过选 US 传 DE 文件、靠后端 400 拦下。判定不出就拒绝并提示怎么改文件名，
 * 不猜、也不给绕过途径：绕过途径正是让界面所示与实际写入的归属产生分歧的来源。
 */
export default function UploadPage() {
  const { message } = App.useApp();
  const [statusItems, setStatusItems] = useState<ImportStatusItem[]>([]);
  /** 最近一次判定结果，只用于界面回显。 */
  const [inspected, setInspected] = useState<FilenameMeta>();
  /**
   * 按文件保存判定结果。Dragger 开了 multiple，一次拖入多个文件时 beforeUpload 会被逐个
   * 调用——若只存一个 state，后一个文件的判定会覆盖前一个，customRequest 就可能拿着 B 的
   * 归属去传 A，把数据写到错误的站点/期次下。用 WeakMap 按文件对象存，天然随文件回收。
   */
  const metaByFile = useRef(new WeakMap<File, FilenameMeta>());

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

  /** 选中文件后先解析文件名：判定得出就回显并放行，判定不出就拒绝并说明该怎么改。 */
  const beforeUpload: UploadProps['beforeUpload'] = async (file) => {
    let meta: FilenameMeta;
    try {
      meta = await inspectFilename(file.name);
    } catch {
      // 解析失败（如非 CSV）已由全局 errorHandler 提示，这里只负责不放行
      return Upload.LIST_IGNORE;
    }
    metaByFile.current.set(file as unknown as File, meta);
    setInspected(meta);

    if (meta.needs.length) {
      message.error(
        `无法从文件名判定归属，请修正文件名后重试。${meta.needs.map((n) => NEED_HINT[n]).join('；')}`,
      );
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const customRequest: UploadProps['customRequest'] = async (options) => {
    const { file, onProgress, onSuccess, onError } = options;
    // 取这个文件自己的判定结果，而不是最近一次的——多文件同传时两者会不一致
    const meta = metaByFile.current.get(file as File);
    // beforeUpload 已保证三项齐备，这里只是不信任地兜一层
    if (!meta?.site || !meta.period) {
      onError?.(new Error('归属未判定'));
      return;
    }
    try {
      await ossDirectUploadAndImport(file as File, {
        site: meta.site,
        period: meta.period,
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
          {/* 归属回显：文件开始上传前就让用户看见它会落到哪一期 */}
          {inspected && (
            <Alert
              type={inspected.needs.length ? 'error' : 'info'}
              showIcon
              style={{ marginBottom: 16 }}
              message={
                inspected.needs.length ? (
                  '无法从文件名判定归属，已拒绝该文件'
                ) : (
                  // 标题与三元组同处一行：判定成功时没有第二层信息，撑成两行只是把
                  // 四个短词摊薄。窄屏放不下时按项换行（wrap），不会挤成一坨。
                  <Space size={[24, 4]} wrap>
                    <Text type="secondary">按文件名判定的归属</Text>
                    <ScopeField label="站点" value={inspected.site} />
                    <ScopeField label="周期" value={PERIOD_LABEL[inspected.period ?? '']} />
                    <ScopeField label="报告日期" value={inspected.report_date} />
                  </Space>
                )
              }
              description={
                inspected.needs.length ? (
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                    {inspected.needs.map((n) => (
                      <li key={n}>{NEED_HINT[n]}</li>
                    ))}
                  </ul>
                ) : undefined
              }
            />
          )}

          <Dragger multiple accept=".csv" beforeUpload={beforeUpload} customRequest={customRequest}>
            <p className="ant-upload-drag-icon">
              <InboxOutlined />
            </p>
            <p className="ant-upload-text">点击或拖拽 CSV 文件到此区域直传</p>
            <p className="ant-upload-hint">
              站点、周期、报告日期均按文件名判定，无需选择；文件名形如
              <code> DE_热门搜索词_简单_Day_2026_07_16.csv</code>。 GB
              级文件经 STS 凭证直传 OSS，不经应用服务器
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
