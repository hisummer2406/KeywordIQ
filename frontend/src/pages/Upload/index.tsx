import { InboxOutlined } from '@ant-design/icons';
import { PageContainer } from '@ant-design/pro-components';
import { Alert, App, Card, Descriptions, Space, Table, Upload } from 'antd';
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

const PERIOD_LABEL: Record<string, string> = { DAY: '日报', WEEK: '周报' };

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
                inspected.needs.length ? '无法从文件名判定归属，已拒绝该文件' : '按文件名判定的归属'
              }
              description={
                inspected.needs.length ? (
                  <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
                    {inspected.needs.map((n) => (
                      <li key={n}>{NEED_HINT[n]}</li>
                    ))}
                  </ul>
                ) : (
                  <Descriptions size="small" column={3} style={{ marginTop: 4 }}>
                    <Descriptions.Item label="站点">{inspected.site}</Descriptions.Item>
                    <Descriptions.Item label="周期">
                      {PERIOD_LABEL[inspected.period ?? '']}
                    </Descriptions.Item>
                    <Descriptions.Item label="报告日期">{inspected.report_date}</Descriptions.Item>
                  </Descriptions>
                )
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
