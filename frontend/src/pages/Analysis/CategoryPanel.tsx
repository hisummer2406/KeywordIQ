import type { CategoryOption } from '@/services/analysis';
import OptionPanel from './OptionPanel';

/** 类目多选面板（过滤区左栏）。形状适配到 OptionPanel。 */
export default function CategoryPanel({
  options,
  value,
  onChange,
}: {
  options: CategoryOption[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <OptionPanel
      options={options.map((o) => ({ name: o.category, count: o.count }))}
      value={value}
      onChange={onChange}
    />
  );
}
