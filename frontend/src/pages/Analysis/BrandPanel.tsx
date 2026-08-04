import type { BrandOption } from '@/services/analysis';
import OptionPanel from './OptionPanel';

/** 品牌多选面板。与类目完全同构——两者都是关键词级排行，前三任一命中。 */
export default function BrandPanel({
  options,
  value,
  onChange,
}: {
  options: BrandOption[];
  value: string[];
  onChange: (next: string[]) => void;
}) {
  return (
    <OptionPanel
      options={options.map((o) => ({ name: o.brand, count: o.count }))}
      value={value}
      onChange={onChange}
    />
  );
}
