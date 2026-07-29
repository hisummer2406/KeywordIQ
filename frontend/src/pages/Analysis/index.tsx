import { PageContainer } from '@ant-design/pro-components';
import { Alert } from 'antd';

/**
 * 分析查询屏（占位）。
 * 阶段四用 ProTable 复刻筛选/分页/类目统计/导出，接后端 V2 查询层适配后的 /api/analysis/search。
 * 依赖：V2 查询层（site-scoped-query）尚未重写——当前后端仍是 V1，屏本体待其就绪后开工。
 */
export default function AnalysisPage() {
  return (
    <PageContainer title="分析查询">
      <Alert
        type="info"
        message="脚手架占位"
        description="阶段四实现：ProTable 复刻查询/筛选/排序/分页、类目统计下拉、导出下载。等待 V2 查询层就绪。"
      />
    </PageContainer>
  );
}
