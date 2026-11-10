import { history, useSearchParams } from '@umijs/max';
import { useMemo, useRef } from 'react';
import CalculatorView, { type OpenTarget } from './CalculatorView';
import { PricingPage, usePricingMeta } from './usePricingMeta';

/**
 * 快速核价（`/pricing/calculate`）。从核价记录打开时带 `?sheet=<id>&version=<id>`，
 * 地址可收藏、刷新后仍停在同一条记录上。
 */
export default function CalculatePage() {
  const { meta, failed } = usePricingMeta();
  const [params] = useSearchParams();
  const sheet = Number(params.get('sheet')) || null;
  const version = Number(params.get('version')) || null;
  // 本页保存后把记录写回地址时，不应再触发一次加载（会用版本输入覆盖正在编辑的表单）
  const located = useRef<string | null>(null);
  const key = sheet ? `${sheet}|${version ?? ''}` : null;
  const openTarget = useMemo<OpenTarget | null>(
    () =>
      sheet && key !== located.current ? { sheetId: sheet, versionId: version, nonce: Date.now() } : null,
    [sheet, version, key],
  );
  return (
    <PricingPage title="快速核价" meta={meta} failed={failed}>
      {(m) => (
        <CalculatorView
          meta={m}
          openTarget={openTarget}
          onLocate={(id, vid) => {
            located.current = id ? `${id}|${vid ?? ''}` : null;
            const next = id ? `?sheet=${id}${vid ? `&version=${vid}` : ''}` : '';
            history.replace(`/pricing/calculate${next}`);
          }}
        />
      )}
    </PricingPage>
  );
}
