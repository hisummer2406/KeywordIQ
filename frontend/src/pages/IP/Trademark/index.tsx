import PendingDataSource from '../PendingDataSource';

export default function TrademarkPage() {
  return (
    <PendingDataSource
      title="商标查询"
      description="本系统目前只导入亚马逊 ABA 搜索词报表，尚无商标数据来源。数据从哪里来（商标局公开接口 / 采购数据集 / 第三方 API）、是落库还是转发查询、「全球」具体覆盖哪些国家和地区，都还没有确定。"
    />
  );
}
