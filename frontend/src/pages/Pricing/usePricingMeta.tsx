import { PageContainer } from '@ant-design/pro-components';
import { Alert, Spin } from 'antd';
import { useCallback, useEffect, useState, type ReactNode } from 'react';
import { getPricingMeta, type PricingMeta } from '@/services/pricing';

/**
 * 定价核算三个子菜单页共用：加载核价元数据（市场、类目、货物属性、规则类别）。
 *
 * 三个功能互不相关，按全系统约定各自是左侧菜单下的独立路由，所以元数据每页各取一次，
 * 不再靠同一个页面里的 Tab 共享状态。
 */
export function usePricingMeta() {
  const [meta, setMeta] = useState<PricingMeta | null>(null);
  const [failed, setFailed] = useState(false);
  const reload = useCallback(() => {
    getPricingMeta()
      .then((m) => {
        setMeta(m);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, []);
  useEffect(reload, [reload]);
  return { meta, failed, reload };
}

export function PricingPage({
  title,
  meta,
  failed,
  children,
}: {
  title: string;
  meta: PricingMeta | null;
  failed: boolean;
  children: (meta: PricingMeta) => ReactNode;
}) {
  return (
    <PageContainer title={title}>
      {meta ? (
        children(meta)
      ) : failed ? (
        <Alert type="error" showIcon message="核价配置加载失败，请稍后刷新重试" />
      ) : (
        <Spin />
      )}
    </PageContainer>
  );
}
