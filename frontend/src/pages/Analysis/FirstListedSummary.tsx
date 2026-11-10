import { Tooltip, Typography } from 'antd';
import { useEffect, useState } from 'react';
import {
  type FirstListedSummary as Summary,
  type FirstListingMode,
  getFirstListed,
  type View,
} from '@/services/analysis';
import { firstListingLabel } from './filters';

/**
 * 当期首次数量，口径随**生效**首次口径（#178）：「首次进入监测」（全部也用它）或「首次进入日榜/周榜」。
 * 标题与表格行标签同一口径，免得数量数的是一种、标签标的是另一种。
 *
 * **跟着表格实际加载的那份切片走**（`loadedSite/View/Date`），不是作用域草稿——草稿切了站点
 * 但没查询时，表格里还是旧站点的行，数量跟着草稿走会和下面的表格对不上。
 *
 * 三种不同的情形必须分开呈现，尤其是第二种不能显示成 0：
 * 1. 有数量（含真的是 0）；
 * 2. 所选期次不晚于监测起点（周视图为窗口含监测起点）——没有对照期，**无法判断**，不是 0；
 * 3. 取数失败/还没取到——不显示数字，也不显示 0。
 */
export interface FirstListedSummaryProps {
  site?: string;
  view?: View;
  date?: string | null;
  mode?: FirstListingMode;
}

const HINT_DAY =
  '本期首次进入监测范围的关键词数量。来自周报的首次入榜只有周精度，不计入任何一天，所以某一天的数字可能少于实际当天新出现的词。';
const HINT_WEEK =
  '本期（周日起、周六止）首次进入监测范围的关键词数量，含日报与周报两个来源，同一个词只计一次。';
const HINT_PERIOD_DAY =
  '本期第一次出现在该站点日榜的关键词数量，只看日榜的历史：此前上过周榜、今天第一次上日榜的词也算。';
const HINT_PERIOD_WEEK =
  '本期第一次出现在该站点周榜的关键词数量，只看周榜的历史，不把日报里的首次折算进来。';

function hintFor(mode: FirstListingMode, view: View): string {
  if (mode === 'PERIOD') return view === 'WEEK' ? HINT_PERIOD_WEEK : HINT_PERIOD_DAY;
  return view === 'WEEK' ? HINT_WEEK : HINT_DAY;
}

export default function FirstListedSummary({
  site,
  view,
  date,
  mode = 'MONITOR',
}: FirstListedSummaryProps) {
  const [summary, setSummary] = useState<Summary | null | undefined>();

  useEffect(() => {
    if (!site || !view || !date) {
      setSummary(undefined);
      return;
    }
    let alive = true;
    setSummary(undefined);
    getFirstListed(site, view, date, mode)
      .then((s) => alive && setSummary(s))
      .catch((e) => {
        // 取数失败只是少一行辅助信息，不该打断主表格；但不能静默——留一笔栈。
        console.error('[Analysis] 首次入榜数量取数失败', e);
      });
    return () => {
      alive = false;
    };
  }, [site, view, date, mode]);

  if (!summary || !view) return null;

  return (
    <Typography.Text type="secondary" data-testid="first-listed-summary">
      <Tooltip title={hintFor(mode, view)}>
        <span>{firstListingLabel(mode, view)}：</span>
      </Tooltip>
      {summary.judgeable ? (
        <>
          <Typography.Text strong>{summary.count}</Typography.Text>
          {summary.previous_period_missing ? (
            <Typography.Text type="warning" style={{ marginInlineStart: 8 }}>
              上一期缺失，部分词可能更早已出现
            </Typography.Text>
          ) : null}
        </>
      ) : (
        <span>
          该期不晚于监测起点，无法判断
          {summary.earliest_known_date ? `（监测起点 ${summary.earliest_known_date}）` : ''}
        </span>
      )}
    </Typography.Text>
  );
}
