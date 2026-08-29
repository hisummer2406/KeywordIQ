import PendingDataSource from '../PendingDataSource';

export default function DesignPatentPage() {
  return (
    <PendingDataSource
      title="外观专利"
      description="本系统目前只导入亚马逊 ABA 搜索词报表，尚无专利数据来源。数据从哪里来（专利局公开接口 / 采购数据集 / 第三方 API）、是落库还是转发查询、覆盖哪些国家和地区，都还没有确定。"
    />
  );
}
