import { Checkbox, Empty, Input, Typography } from 'antd';
import { useMemo, useState } from 'react';


/**
 * 带计数的多选面板。品牌与类目共用——两者是同构的关键词级排行，交互没有任何理由不同。
 *
 * 抽出来而不是复制一份：两份相似面板会各自漂移（过滤框的行为、全选语义、空态文案），
 * 而用户会把它们当成同一种控件来用。
 *
 * 选项数可达数百（品牌可达数万），逐个滚动查找不可行——所以有按名过滤的输入与限高滚动。
 * **计数不是装饰**：品牌名高度相近（Anker / Anker Direct / Ankermann），计数是用户判断
 * 该选哪个的唯一依据。受控组件：清单由外部传入，自身不取数。
 */

export interface OptionPanelProps {
  options: { name: string; count: number }[];
  value: string[];
  onChange: (next: string[]) => void;
}

export default function OptionPanel({ options, value, onChange }: OptionPanelProps) {
  const [q, setQ] = useState('');

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? options.filter((o) => o.name.toLowerCase().includes(needle)) : options;
  }, [options, q]);

  const allChecked = options.length > 0 && value.length === options.length;

  return (
    <div>
      <Input.Search
        size="middle"
        allowClear
        placeholder="搜索类目"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        style={{ marginBottom: 8 }}
      />
      <Checkbox
        checked={allChecked}
        indeterminate={value.length > 0 && !allChecked}
        onChange={(e) => onChange(e.target.checked ? options.map((o) => o.name) : [])}
        disabled={!options.length}
      >
        全部类目
      </Checkbox>
      {/* 表格展示前三名商品，类目筛选只认第一名——不标注的话，用户会拿卡牌里第二名的
          类目回填条件，得到空结果并误以为是数据缺失 */}
      <div
        style={{
          maxHeight: 380,
          overflowY: 'auto',
          marginTop: 8,
          paddingRight: 4,
          borderTop: '1px solid #f0f0f0',
        }}
      >
        {visible.length === 0 ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={options.length ? '无匹配类目' : '该站点该视图暂无类目'}
          />
        ) : (
          <Checkbox.Group
            value={value}
            onChange={(next) => onChange(next as string[])}
            style={{ display: 'flex', flexDirection: 'column', gap: 6, paddingTop: 8 }}
          >
            {visible.map((o) => (
              <Checkbox key={o.name} value={o.name}>
                <span title={o.name}>{o.name}</span>{' '}
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  ({o.count})
                </Typography.Text>
              </Checkbox>
            ))}
          </Checkbox.Group>
        )}
      </div>
    </div>
  );
}
