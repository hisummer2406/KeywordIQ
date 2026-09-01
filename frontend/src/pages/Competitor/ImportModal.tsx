import { InboxOutlined } from '@ant-design/icons';
import {
  Alert,
  Button,
  Descriptions,
  Input,
  Modal,
  Radio,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd';
import type { RadioChangeEvent } from 'antd';
import type { UploadFile } from 'antd';
import { useState } from 'react';
import {
  commitImport,
  previewImport,
  type CommitMode,
  type CommitResult,
  type CompetitorGroup,
  type PreviewResponse,
} from '@/services/competitor';
import type { SiteOption } from '@/services/upload';
import { formatSiteLabel } from '@/pages/Analysis/siteLabel';

/**
 * 添加卖家的唯一入口:粘贴文本 或 导入书签,两条路径共用同一个预览→确认流程
 * （spec.md「导入走预览再确认两步，不允许一次性直接落库」）。
 *
 * **没有第三种"直接添加"路径。** 哪怕只加一个卖家,也要走预览——这样才能在落库前告诉
 * 用户"这个链接到底能不能解析"，而不是让一次手滑的粘贴静默失败或静默算错站点。
 */

const { Dragger } = Upload;

interface Props {
  open: boolean;
  groups: CompetitorGroup[];
  sites: SiteOption[];
  onClose: () => void;
  onImported: (result: CommitResult) => void;
}

type Step = 'input' | 'preview';

export default function ImportModal({ open, groups, sites, onClose, onImported }: Props) {
  const [step, setStep] = useState<Step>('input');
  const [sourceTab, setSourceTab] = useState<'text' | 'file'>('text');
  const [rawText, setRawText] = useState('');
  const [file, setFile] = useState<File | undefined>();
  const [fallbackSite, setFallbackSite] = useState<string | undefined>();
  const [preview, setPreview] = useState<PreviewResponse | undefined>();
  const [mode, setMode] = useState<CommitMode>('append');
  const [targetGroupId, setTargetGroupId] = useState<number | undefined>();
  const [busy, setBusy] = useState(false);

  const reset = () => {
    setStep('input');
    setSourceTab('text');
    setRawText('');
    setFile(undefined);
    setFallbackSite(undefined);
    setPreview(undefined);
    setMode('append');
    setTargetGroupId(undefined);
  };

  const close = () => {
    reset();
    onClose();
  };

  const canPreview = sourceTab === 'text' ? rawText.trim().length > 0 : !!file;

  const runPreview = async () => {
    setBusy(true);
    try {
      const res = await previewImport({
        rawText: sourceTab === 'text' ? rawText : undefined,
        file: sourceTab === 'file' ? file : undefined,
        fallbackSite,
      });
      setPreview(res);
      setStep('preview');
    } catch {
      // 失败提示由 app.tsx 的 errorHandler 唯一出口负责（超配额/校验失败都在这里现形）。
      // **catch 不能省**：Button 的 onClick 不消费返回的 promise，少了它就是一条
      // unhandled rejection——浏览器里进控制台，vitest 里让整个进程退出码非 0。
    } finally {
      setBusy(false);
    }
  };

  const runCommit = async () => {
    if (!preview) return;
    if (mode === 'replace_group' && !targetGroupId) return;
    setBusy(true);
    try {
      const result = await commitImport({
        preview_token: preview.preview_token,
        mode,
        target_group_id: targetGroupId ?? null,
      });
      message.success(
        `已导入:新建卖家 ${result.created_sellers} 个、新增关注 ${result.created_watches} 个、` +
          `更新 ${result.updated_watches} 个${result.removed_watches ? `、清空重建 ${result.removed_watches} 个` : ''}`,
      );
      onImported(result);
      close();
    } catch {
      // 同上：token 过期/超配额等失败,弹窗留在预览步以便用户重试或返回上一步。
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      open={open}
      title="添加卖家"
      width={720}
      onCancel={close}
      destroyOnHidden
      footer={
        step === 'input' ? (
          <Space>
            <Button onClick={close}>取消</Button>
            <Button type="primary" loading={busy} disabled={!canPreview} onClick={runPreview}>
              预览
            </Button>
          </Space>
        ) : (
          <Space>
            <Button onClick={() => setStep('input')}>上一步</Button>
            <Button
              type="primary"
              loading={busy}
              disabled={
                !preview ||
                preview.parsed === 0 ||
                (mode === 'replace_group' && !targetGroupId)
              }
              onClick={runCommit}
            >
              确认导入
            </Button>
          </Space>
        )
      }
    >
      {step === 'input' && (
        <>
          <Tabs
            activeKey={sourceTab}
            onChange={(k) => setSourceTab(k as 'text' | 'file')}
            items={[
              {
                key: 'text',
                label: '粘贴文本',
                children: (
                  <Input.TextArea
                    rows={8}
                    placeholder={
                      '一行一个链接或卖家 ID,例如:\n' +
                      'https://www.amazon.com/s?i=merchant-items&me=A1TZ946F8HZTNY\n' +
                      'A1TZ946F8HZTNY'
                    }
                    value={rawText}
                    onChange={(e) => setRawText(e.target.value)}
                  />
                ),
              },
              {
                key: 'file',
                label: '导入书签',
                children: (
                  <Dragger
                    accept=".html,.htm"
                    maxCount={1}
                    beforeUpload={(f: UploadFile | File) => {
                      setFile(f as File);
                      return false;
                    }}
                    onRemove={() => setFile(undefined)}
                    fileList={file ? [{ uid: '1', name: file.name } as UploadFile] : []}
                  >
                    <p className="ant-upload-drag-icon">
                      <InboxOutlined />
                    </p>
                    <p className="ant-upload-text">点击或拖拽浏览器导出的书签 HTML 文件</p>
                    <p className="ant-upload-hint">
                      Chrome/Edge：书签管理器 → 右上角菜单 → 导出书签
                    </p>
                  </Dragger>
                ),
              },
            ]}
          />
          <Space direction="vertical" style={{ width: '100%', marginTop: 12 }}>
            <Typography.Text type="secondary">
              默认站点(可选):部分链接不带站点信息(裸卖家 ID、缺少站点参数的链接),这些条目会
              用这里选的站点兜底;不选则这些条目判为解析失败。
            </Typography.Text>
            <Select
              allowClear
              style={{ width: 240 }}
              placeholder="不设默认站点"
              value={fallbackSite}
              onChange={setFallbackSite}
              options={sites.map((s) => ({ label: formatSiteLabel(s), value: s.code }))}
            />
          </Space>
        </>
      )}

      {step === 'preview' && preview && (
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Descriptions size="small" column={3} bordered>
            <Descriptions.Item label="解析出">{preview.parsed} 个卖家</Descriptions.Item>
            <Descriptions.Item label="新增">{preview.new} 个</Descriptions.Item>
            <Descriptions.Item label="已关注">{preview.existing} 个</Descriptions.Item>
          </Descriptions>

          {preview.unparsable.length > 0 && (
            <Alert
              type="warning"
              showIcon
              message={`${preview.unparsable.length} 条无法解析`}
              description={
                <Table
                  size="small"
                  pagination={false}
                  rowKey={(r) => r.text}
                  dataSource={preview.unparsable}
                  scroll={{ y: 160 }}
                  columns={[
                    { title: '原文', dataIndex: 'text', ellipsis: true },
                    { title: '原因', dataIndex: 'reason', width: 220 },
                  ]}
                />
              }
            />
          )}

          {preview.groups.length > 0 && (
            <div>
              <Typography.Text type="secondary">
                从书签文件夹识别出的候选分组(append 模式下自动建组/复用同名分组):
              </Typography.Text>
              <div style={{ marginTop: 4 }}>
                {preview.groups.map((g) => (
                  <Tag key={g}>{g}</Tag>
                ))}
              </div>
            </div>
          )}

          {preview.samples.length > 0 && (
            <Table
              size="small"
              pagination={false}
              rowKey={(r) => `${r.seller_id}-${r.site}`}
              dataSource={preview.samples}
              title={() => `抽样预览(前 ${preview.samples.length} 条)`}
              columns={[
                { title: '卖家 ID', dataIndex: 'seller_id' },
                { title: '站点', dataIndex: 'site', width: 70 },
                {
                  title: '状态',
                  dataIndex: 'is_new',
                  width: 90,
                  render: (v: boolean) =>
                    v ? <Tag color="blue">新增</Tag> : <Tag>已关注</Tag>,
                },
                {
                  title: '候选分组',
                  dataIndex: 'group_name',
                  render: (v: string | null) => v || '—',
                },
              ]}
            />
          )}

          <div>
            <Typography.Text strong>确认方式</Typography.Text>
            <div style={{ marginTop: 8 }}>
              <Radio.Group
                value={mode}
                onChange={(e: RadioChangeEvent) => setMode(e.target.value as CommitMode)}
              >
                <Radio value="append">追加(默认)</Radio>
                <Radio value="replace_group">对齐分组(清空重建)</Radio>
              </Radio.Group>
            </div>
            {mode === 'append' ? (
              <Space direction="vertical" style={{ marginTop: 8 }}>
                <Typography.Text type="secondary">
                  未识别出候选分组的卖家归入(可选,已有分组/备注的已关注卖家不受影响):
                </Typography.Text>
                <Select
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  style={{ width: 240 }}
                  placeholder="不分组"
                  value={targetGroupId}
                  onChange={setTargetGroupId}
                  options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
                />
              </Space>
            ) : (
              <Space direction="vertical" style={{ marginTop: 8, width: '100%' }}>
                <Typography.Text type="secondary">
                  选择要对齐的分组:该分组下现有的全部关注会被清空,替换为本次预览结果。
                </Typography.Text>
                {/*
                  showSearch 在这里不是锦上添花：书签导入按文件夹名自动建组，一份真实
                  书签一次就能建出 37 个组，名字还多是「7.17」「竞6.12」这类难以区分的
                  短名（虚拟列表一次只渲染 10 项，实测要滚 5 屏）。而这个下拉选中的组
                  会被**整组清空重建**——滚动查找相似名字时选错，代价是清空另一个分组。
                  下拉本身写明了后果，但降低不了选错的概率，搜索才能。

                  optionFilterProp 必须显式给 label：options 形态下 antd 默认按 value
                  过滤，而 value 是 group_id，表现为「搜什么都搜不到」且不报错。
                */}
                <Select
                  showSearch
                  optionFilterProp="label"
                  style={{ width: 240 }}
                  placeholder="选择分组"
                  value={targetGroupId}
                  onChange={setTargetGroupId}
                  options={groups.map((g) => ({ label: g.name, value: g.group_id }))}
                />
              </Space>
            )}
          </div>
        </Space>
      )}
    </Modal>
  );
}
