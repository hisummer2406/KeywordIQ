import { useAccess } from '@umijs/max';
import RatesManager from './RatesManager';
import { PricingPage, usePricingMeta } from './usePricingMeta';

/** 费用配置（`/pricing/rates`）。维护按钮按 pricing:manage-rates 显示，后端逐端点再校验。 */
export default function RatesPage() {
  const access = useAccess() as { canManagePricingRates?: boolean };
  const { meta, failed, reload } = usePricingMeta();
  return (
    <PricingPage title="费用配置" meta={meta} failed={failed}>
      {(m) => <RatesManager meta={m} canManage={!!access.canManagePricingRates} onChanged={reload} />}
    </PricingPage>
  );
}
