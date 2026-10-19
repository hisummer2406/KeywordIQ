import { Tooltip, Typography } from 'antd';
import { useEffect, useState } from 'react';
import { type FirstListedSummary as Summary, getFirstListed, type View } from '@/services/analysis';

/**
 * 当期「首次入榜」数量：本期首次进入监测范围的关键词数量。
 *
 * **跟着表格实际加载的那份切片走**（`loadedSite/View/Date`），不是作用域草稿——草稿切了站点
 * 但没查询时，表格里还是旧站点的行，数量跟着草稿走会和下面的表格对不上。
 *
 * 三种不同的情形必须分开呈现，尤其是第二种不能显示成 0：
 * 1. 有数量（含真的是 0）；
 * 2. 所选期次是监测起点——所有词都只是「最早可观测」，**无法判断**，不是 0；
 * 3. 取数失败/还没取到——不显示数字，也不显示 0。
 */
export interface FirstListedSummaryProps {
  site?: string;
  view?: View;
  date?: string | null;
}

const HINT_DAY =
  '本期首次进入监测范围的关键词数量。来自周报的首次入榜只有周精度，不计入任何一天，所以某一天的数字可能少于实际当天新出现的词。';
const HINT_WEEK =
  '本期（周日起、周六止）首次进入监测范围的关键词数量，含日报与周报两个来源，同一个词只计一次。';

export default function FirstListedSummary({ site, view, date }: FirstListedSummaryProps) {
  const [summary, setSummary] = useState<Summary | null | undefined>();

  useEffect(() => {
    if (!site || !view || !date) {
      setSummary(undefined);
      return;
    }
    let alive = true;
    setSummary(undefined);
    getFirstListed(site, view, date)
      .then((s) => alive && setSummary(s))
      .catch((e) => {
        // 取数失败只是少一行辅助信息，不该打断主表格；但不能静默——留一笔栈。
        console.error('[Analysis] 首次入榜数量取数失败', e);
      });
    return () => {
      alive = false;
    };
  }, [site, view, date]);

  if (!summary) return null;

  return (
    <Typography.Text type="secondary" data-testid="first-listed-summary">
      <Tooltip title={view === 'WEEK' ? HINT_WEEK : HINT_DAY}>
        <span>首次入榜：</span>
      </Tooltip>
      {summary.judgeable ? (
        <Typography.Text strong>{summary.count}</Typography.Text>
      ) : (
        <span>
          该期是监测起点，无法判断首次入榜
          {summary.earliest_known_date ? `（监测始于 ${summary.earliest_known_date}）` : ''}
        </span>
      )}
    </Typography.Text>
  );
}
