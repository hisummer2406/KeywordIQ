import { PageContainer } from '@ant-design/pro-components';
import { Alert } from 'antd';

/**
 * 数据上传屏（占位）。
 * 阶段五用 AntD Upload + customRequest + ali-oss 实现 STS 分片直传，接 /api/upload/oss/*，
 * 复用 /processing-status 轮询导入状态。后端 OSS 契约已就绪。
 */
export default function UploadPage() {
  return (
    <PageContainer title="数据上传">
      <Alert
        type="info"
        message="脚手架占位"
        description="阶段五实现：日报/周报上传，Upload customRequest → 换 STS 凭证 → ali-oss 分片直传 → 回传 key → 轮询导入状态。"
      />
    </PageContainer>
  );
}
