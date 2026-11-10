import { history } from '@umijs/max';
import RecordsList from './RecordsList';
import { PricingPage, usePricingMeta } from './usePricingMeta';

/** 核价记录（`/pricing/records`）。打开某条记录跳到快速核价并带上记录地址参数。 */
export default function RecordsPage() {
  const { meta, failed } = usePricingMeta();
  return (
    <PricingPage title="核价记录" meta={meta} failed={failed}>
      {(m) => (
        <RecordsList
          meta={m}
          reloadKey={0}
          onOpen={(sheetId, versionId) =>
            history.push(`/pricing/calculate?sheet=${sheetId}${versionId ? `&version=${versionId}` : ''}`)
          }
        />
      )}
    </PricingPage>
  );
}
