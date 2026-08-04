import { Checkbox, Empty, Input, Tooltip, Typography } from 'antd';
import { useMemo, useState } from 'react';
import type { CategoryOption } from '@/services/analysis';
import { TOP1_ONLY_HINT } from './FilterPanel';

/**
 * 类目多选面板（过滤区左栏）。
 *
 * 类目数可达数百，逐个滚动查找不可行——所以有按名过滤的输入与限高滚动。
 * 受控组件：类目清单由外部传入，自身不取数。
 */

export interface CategoryPanelProps {
  options: CategoryOption[];
  value: string[];
  onChange: (next: string[]) => void;
}

export default function CategoryPanel({ options, value, onChange }: CategoryPanelProps) {
  const [q, setQ] = useState('');

  const visible = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle ? options.filter((o) => o.category.toLowerCase().includes(needle)) : options;
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
        onChange={(e) => onChange(e.target.checked ? options.map((o) => o.category) : [])}
        disabled={!options.length}
      >
        全部类目
      </Checkbox>
      {/* 表格展示前三名商品，类目筛选只认第一名——不标注的话，用户会拿卡牌里第二名的
          类目回填条件，得到空结果并误以为是数据缺失 */}
      <Tooltip title={TOP1_ONLY_HINT}>
        <Typography.Text
          type="secondary"
          style={{
            display: 'block',
            fontSize: 12,
            marginTop: 2,
            cursor: 'help',
          }}
        >
          仅按点击量第一名的类目筛选
        </Typography.Text>
      </Tooltip>
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
              <Checkbox key={o.category} value={o.category}>
                <span title={o.category}>{o.category}</span>{' '}
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
