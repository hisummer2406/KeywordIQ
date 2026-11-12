import { Button, Form, Input, Radio, Select, Space, Typography, theme } from 'antd';
import { useEffect, useRef, useState } from 'react';
import { type KeywordGroup, listGroups } from '@/services/library';
import { createDossier } from '@/services/opportunityDossier';
import { newToken } from '@/services/risk';
import { parseKeywordInput } from './dossierView';

/**
 * 新建机会档案。两种来源：手动填关键词，或从「我的词库」的一个分组带入（运营本来就按产品想法建分组）。
 * 建档即生成第一份 ABA 证据快照，不产生任何外部调用。
 */

const MAX_KEYWORDS = 20;

interface Props {
  sites: { code: string; name: string }[];
  /** 从词库分组入口进来时预选的分组 */
  groupId: number | null;
  /** 从线索带入的站点与关键词（change add-opportunity-signal-leads） */
  preset?: { site: string; keywords: string[] } | null;
  onCreated: (id: number) => void;
  onCancel: () => void;
}

interface Values {
  title: string;
  idea?: string;
  source: 'manual' | 'group';
  site?: string;
  keywords_text?: string;
  group_id?: number;
}

export default function NewDossier({ sites, groupId, preset, onCreated, onCancel }: Props) {
  const { token: t } = theme.useToken();
  const [form] = Form.useForm<Values>();
  const [groups, setGroups] = useState<KeywordGroup[]>([]);
  const [sending, setSending] = useState(false);
  const token = useRef(newToken());
  const source = Form.useWatch('source', form) ?? (groupId ? 'group' : 'manual');
  const chosenGroupId = Form.useWatch('group_id', form);
  const chosenGroup = groups.find((g) => g.group_id === chosenGroupId);
  const keywords = parseKeywordInput(Form.useWatch('keywords_text', form) ?? '');

  useEffect(() => {
    listGroups()
      .then((gs) => {
        setGroups(gs);
        const preset = gs.find((g) => g.group_id === groupId);
        // 分组名通常就是产品想法，带进来当默认标题
        if (preset && !form.getFieldValue('title')) form.setFieldValue('title', preset.name.slice(0, 100));
      })
      .catch(() => undefined);
  }, [groupId, form]);

  const submit = async () => {
    const v = await form.validateFields();
    setSending(true);
    try {
      const r = await createDossier({
        title: v.title.trim(),
        idea: v.idea?.trim() || null,
        client_token: token.current,
        ...(v.source === 'group'
          ? { keyword_group_id: v.group_id, keywords: [] }
          : { site: v.site, keywords: parseKeywordInput(v.keywords_text ?? '') }),
      });
      token.current = newToken();
      onCreated(r.id);
    } finally {
      setSending(false);
    }
  };

  return (
    <div style={{ padding: '32px 24px', height: '100%', overflowY: 'auto' }}>
      <div style={{ maxWidth: 720, margin: '0 auto' }}>
        <Typography.Title level={4}>新建机会档案</Typography.Title>
        <Typography.Paragraph type="secondary">
          一个档案对应一个产品想法和它的一组关键词。建好后会立刻生成 ABA 证据；搜索量、销量、售价这些系统没有的数据，可以在档案里补上带来源的资料。
        </Typography.Paragraph>
        {preset && (
          <Typography.Paragraph type="secondary">已从线索带入 {preset.site} 站点的 {preset.keywords.length} 个词，起个标题、写下想法即可。</Typography.Paragraph>
        )}
        <div style={{ border: `1px solid ${t.colorBorder}`, borderRadius: 12, padding: 20, background: t.colorBgContainer }}>
          <Form<Values>
            form={form}
            layout="vertical"
            requiredMark={false}
            initialValues={{
              source: groupId ? 'group' : 'manual',
              group_id: groupId ?? undefined,
              site: preset?.site ?? sites[0]?.code,
              keywords_text: preset?.keywords.join('\n'),
            }}
            onValuesChange={() => {
              token.current = newToken();
            }}
          >
            <Form.Item name="title" label="标题" rules={[{ required: true, whitespace: true, message: '写一个标题' }, { max: 100 }]}>
              <Input placeholder="例如：圣诞巧克力融化杯" maxLength={100} />
            </Form.Item>
            <Form.Item name="idea" label="产品想法（可选）" rules={[{ max: 4000 }]}>
              <Input.TextArea autoSize={{ minRows: 2, maxRows: 6 }} placeholder="想做什么产品、和现有商品有什么不同、为什么现在看它" />
            </Form.Item>
            <Form.Item name="source" label="关键词来源">
              <Radio.Group
                optionType="button"
                buttonStyle="solid"
                options={[
                  { value: 'manual', label: '手动填写' },
                  { value: 'group', label: '从我的词库分组带入' },
                ]}
              />
            </Form.Item>
            {source === 'manual' ? (
              <>
                <Form.Item name="site" label="站点" rules={[{ required: true, message: '选择站点' }]}>
                  <Select style={{ width: 200 }} options={sites.map((s) => ({ value: s.code, label: `${s.name}（${s.code}）` }))} />
                </Form.Item>
                <Form.Item
                  name="keywords_text"
                  label={`关键词（每行一个，最多 ${MAX_KEYWORDS} 个）`}
                  extra={keywords.length > 0 && `已识别 ${keywords.length} 个。没进过 ABA 榜单的词也会保留，并标明没有记录。`}
                  rules={[
                    {
                      validator: async (_, v: string) => {
                        const n = parseKeywordInput(v ?? '').length;
                        if (n === 0) throw new Error('至少填一个关键词');
                        if (n > MAX_KEYWORDS) throw new Error(`最多 ${MAX_KEYWORDS} 个关键词，现在是 ${n} 个`);
                      },
                    },
                  ]}
                >
                  <Input.TextArea autoSize={{ minRows: 4, maxRows: 12 }} placeholder={'melty mug\nchocolate melting cup\nmeltzy mug'} />
                </Form.Item>
              </>
            ) : (
              <Form.Item
                name="group_id"
                label="词库分组"
                rules={[{ required: true, message: '选择一个分组' }]}
                extra={
                  chosenGroup &&
                  (chosenGroup.item_count > MAX_KEYWORDS
                    ? `这个分组有 ${chosenGroup.item_count} 个词，超过 ${MAX_KEYWORDS} 个的上限，请先在词库里精简或改用手动填写。`
                    : chosenGroup.item_count === 0
                      ? '这个分组里还没有词。'
                      : `将带入 ${chosenGroup.site} 站点的 ${chosenGroup.item_count} 个词。`)
                }
              >
                <Select
                  showSearch
                  optionFilterProp="label"
                  placeholder={groups.length ? '选择分组' : '你的词库里还没有分组'}
                  options={groups.map((g) => ({ value: g.group_id, label: `${g.name}（${g.site} · ${g.item_count} 个词）` }))}
                />
              </Form.Item>
            )}
          </Form>
          <Space>
            <Button type="primary" loading={sending} onClick={() => submit().catch(() => undefined)}>
              建档并生成证据
            </Button>
            <Button onClick={onCancel}>取消</Button>
          </Space>
        </div>
      </div>
    </div>
  );
}
