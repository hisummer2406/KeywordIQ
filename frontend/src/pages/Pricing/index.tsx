import { PageContainer } from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Alert, Spin, Tabs } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { getPricingMeta, type PricingMeta } from '@/services/pricing';
import CalculatorTab, { type OpenTarget } from './CalculatorTab';
import RatesTab from './RatesTab';
import RecordsTab from './RecordsTab';

/**
 * 定价核算（change add-pricing-calculation）。三个页签：快速核价 / 核价记录 / 费用配置。
 *
 * 全宽布局，不加挤占比较表的侧栏。核价公式只在后端，本页只负责输入与展示。
 */
export default function PricingPage() {
  const access = useAccess() as { canManagePricingRates?: boolean };
  const [meta, setMeta] = useState<PricingMeta | null>(null);
  const [failed, setFailed] = useState(false);
  const [tab, setTab] = useState('calculate');
  const [openTarget, setOpenTarget] = useState<OpenTarget | null>(null);
  const [recordsKey, setRecordsKey] = useState(0);

  const loadMeta = useCallback(() => {
    getPricingMeta()
      .then((m) => {
        setMeta(m);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, []);

  useEffect(loadMeta, [loadMeta]);

  if (!meta) {
    return (
      <PageContainer title="定价核算">
        {failed ? <Alert type="error" showIcon message="核价配置加载失败，请稍后刷新重试" /> : <Spin />}
      </PageContainer>
    );
  }

  return (
    <PageContainer title="定价核算">
      <Tabs
        activeKey={tab}
        onChange={setTab}
        items={[
          {
            key: 'calculate',
            label: '快速核价',
            children: (
              <CalculatorTab meta={meta} openTarget={openTarget} onSheetChanged={() => setRecordsKey((k) => k + 1)} />
            ),
          },
          {
            key: 'records',
            label: '核价记录',
            children: (
              <RecordsTab
                meta={meta}
                reloadKey={recordsKey}
                onOpen={(sheetId, versionId) => {
                  setOpenTarget({ sheetId, versionId, nonce: Date.now() });
                  setTab('calculate');
                }}
              />
            ),
          },
          {
            key: 'rates',
            label: '费用配置',
            children: (
              <RatesTab meta={meta} canManage={!!access.canManagePricingRates} onChanged={loadMeta} />
            ),
          },
        ]}
      />
    </PageContainer>
  );
}
