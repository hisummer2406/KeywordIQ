import { PageContainer } from '@ant-design/pro-components';
import { history, useAccess, useParams } from '@umijs/max';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Image,
  Popconfirm,
  Result,
  Row,
  Space,
  Spin,
  Tag,
  Typography,
  message,
} from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  type CheckDetail,
  getCheck,
  getCheckImage,
  getRiskMeta,
  newToken,
  requestDetails,
  requestNextPage,
  requestRetry,
  type RiskMeta,
} from '@/services/risk';
import { coverageText, dt, errorText, TASK_STATUS } from '../riskLabels';
import CandidateTable from './CandidateTable';
import ReviewPanel from './ReviewPanel';

/**
 * 外观专利核查详情（独立路由，刷新/书签可恢复）。
 *
 * 只有「读取下一页」「重新检索」「补充详情」三个按钮会产生智慧芽调用，且都先说明
 * 调用组成。任务在途时只轮询内部状态（GET 详情），不会重新触发外部调用。
 */

const POLL_MS = 3000;

export default function RecordDetailPage() {
  const access = useAccess();
  const { id } = useParams<{ id: string }>();
  const checkId = Number(id);
  const [data, setData] = useState<CheckDetail | null>(null);
  const [meta, setMeta] = useState<RiskMeta | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [image, setImage] = useState<string | undefined>();
  const [acting, setActing] = useState(false);
  const pageToken = useRef(newToken());
  const retryToken = useRef(newToken());

  const load = useCallback(async () => {
    try {
      setData(await getCheck(checkId));
    } catch (e: any) {
      if (e?.response?.status === 404) setNotFound(true);
    }
  }, [checkId]);

  useEffect(() => {
    if (!Number.isFinite(checkId)) {
      setNotFound(true);
      return;
    }
    load();
    getRiskMeta().then(setMeta).catch(() => undefined);
    let url: string | undefined;
    getCheckImage(checkId)
      .then((blob) => {
        url = URL.createObjectURL(blob);
        setImage(url);
      })
      .catch(() => undefined);
    return () => {
      if (url) URL.revokeObjectURL(url);
    };
  }, [checkId, load]);

  const busy = (data?.active_kinds.length ?? 0) > 0;
  useEffect(() => {
    if (!busy) return;
    const t = setInterval(() => load().catch(() => undefined), POLL_MS);
    return () => clearInterval(t);
  }, [busy, load]);

  const act = async (fn: () => Promise<unknown>, done: () => void) => {
    setActing(true);
    try {
      await fn();
      done();
      await load();
    } finally {
      setActing(false);
    }
  };

  if (notFound) {
    return (
      <PageContainer title="核查详情">
        <Result status="404" title="核查记录不存在" extra={<Button onClick={() => history.push('/ip/records')}>返回核查记录</Button>} />
      </PageContainer>
    );
  }
  if (!data) {
    return (
      <PageContainer title="核查详情">
        <Card><Spin /></Card>
      </PageContainer>
    );
  }

  const status = TASK_STATUS[data.search_status];
  const lastSearch = data.tasks.find((t) => t.kind === 'search');
  const lastDetail = data.tasks.find((t) => t.kind === 'detail');
  const marketName = (code: string) => meta?.markets.find((m) => m.code === code)?.name ?? code;
  const noHit = data.search_completed && data.total_count === 0;

  return (
    <PageContainer
      title={`${data.product_name} · ${data.product_version}`}
      onBack={() => history.push('/ip/records')}
      extra={
        <Space>
          {access.canSearchRisk && (
            <Button onClick={() => history.push(`/ip/patent?from=${data.id}`)}>基于本次发起新一轮</Button>
          )}
        </Space>
      }
    >
      <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
        <Card title="核查输入">
          <Row gutter={24}>
            <Col xs={24} md={16}>
              <Descriptions column={1} size="small">
                <Descriptions.Item label="核查编号">{data.id}</Descriptions.Item>
                <Descriptions.Item label="销售市场">{data.markets.map(marketName).join('、')}</Descriptions.Item>
                <Descriptions.Item label="检索法域">
                  <Space orientation="vertical" size={0}>
                    {data.jurisdiction_details.map((j) => (
                      <span key={j.code}>
                        {j.name}
                        {!j.verified && <Tag style={{ marginLeft: 6 }}>待核实</Tag>}
                        {j.note && <Typography.Text type="secondary">{`（${j.note}）`}</Typography.Text>}
                      </span>
                    ))}
                  </Space>
                </Descriptions.Item>
                {data.source_ref && <Descriptions.Item label="来源">{data.source_ref}</Descriptions.Item>}
                {data.description && <Descriptions.Item label="说明">{data.description}</Descriptions.Item>}
                <Descriptions.Item label="创建时间">{dt(data.created_at)}</Descriptions.Item>
                {data.previous_check_id && (
                  <Descriptions.Item label="上一轮">
                    <Typography.Link onClick={() => history.push(`/ip/records/${data.previous_check_id}`)}>
                      {`#${data.previous_check_id}`}
                    </Typography.Link>
                  </Descriptions.Item>
                )}
                {data.next_check_ids.length > 0 && (
                  <Descriptions.Item label="后续轮次">
                    <Space>
                      {data.next_check_ids.map((n) => (
                        <Typography.Link key={n} onClick={() => history.push(`/ip/records/${n}`)}>{`#${n}`}</Typography.Link>
                      ))}
                    </Space>
                  </Descriptions.Item>
                )}
              </Descriptions>
            </Col>
            <Col xs={24} md={8}>
              {image ? <Image src={image} width="100%" alt="产品原图" /> : <Typography.Text type="secondary">原图加载中</Typography.Text>}
            </Col>
          </Row>
        </Card>

        <Card
          title={
            <Space>
              <span>检索结果</span>
              <Tag color={status.color}>{status.text}</Tag>
            </Space>
          }
          extra={
            <Typography.Text type="secondary">
              {coverageText(data.fetched_count, data.total_count)}
              {data.searched_at ? ` · 最近检索 ${dt(data.searched_at)}` : ''}
            </Typography.Text>
          }
        >
          <Space orientation="vertical" style={{ width: '100%' }}>
            {busy && <Alert type="info" showIcon title="任务执行中，页面会自动刷新状态（不会重复调用智慧芽）。" />}
            {(data.search_status === 'failed' || data.search_status === 'unknown') && (
              <Alert
                type={data.search_status === 'unknown' ? 'warning' : 'error'}
                showIcon
                title={errorText(lastSearch?.error_category) || status.text}
                description={
                  data.search_status === 'unknown'
                    ? '系统不会自动重发。如需重新检索，请点击「重新检索」——这会产生新的调用。'
                    : undefined
                }
              />
            )}
            {lastDetail && (lastDetail.status === 'failed' || lastDetail.status === 'unknown') && (
              <Alert type="warning" showIcon title={`最近一次读取详情：${errorText(lastDetail.error_category)}`} />
            )}
            {noHit && (
              <Alert
                type="info"
                showIcon
                title="本次检索范围（所选法域）内未找到相似外观专利"
                description="这只说明本次范围内没有命中，不代表低风险或可以上架；请在下方按市场填写人工意见。"
              />
            )}
            {access.canSearchRisk && (
              <Space wrap>
                {data.can_fetch_more && (
                  <Popconfirm
                    title="读取下一页"
                    description={`将再调用一次 P060 检索（offset ${data.fetched_count}，${data.page_size} 条）；临时图片地址过期时还会多一次 P010 上传。`}
                    onConfirm={() => act(() => requestNextPage(data.id, pageToken.current), () => {
                      pageToken.current = newToken();
                    })}
                  >
                    <Button loading={acting}>读取下一页</Button>
                  </Popconfirm>
                )}
                {data.can_retry && (
                  <Popconfirm
                    title="重新检索"
                    description="上一次结果失败或未知，重新检索会产生新的调用（P060，必要时加 P010）。"
                    onConfirm={() => act(() => requestRetry(data.id, retryToken.current), () => {
                      retryToken.current = newToken();
                    })}
                  >
                    <Button loading={acting} danger>重新检索</Button>
                  </Popconfirm>
                )}
              </Space>
            )}
            {data.candidates.length > 0 && (
              <CandidateTable
                checkId={data.id}
                candidates={data.candidates}
                originalImage={image}
                canSearch={!!access.canSearchRisk}
                detailBusy={data.active_kinds.includes('detail')}
                maxBatch={meta?.max_detail_batch ?? 20}
                maxFileBatch={meta?.max_file_detail_batch ?? 10}
                onRequestDetails={async (ids, kinds) => {
                  const r = await requestDetails(data.id, { client_token: newToken(), candidate_ids: ids, kinds });
                  if (r.skipped) message.info('所选候选的详情都已获取，未产生新调用');
                  await load();
                }}
              />
            )}
            {data.calls.length > 0 && (
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {`智慧芽调用记录：${data.calls.map((c) => `${c.api} ${c.outcome === 'success' ? '成功' : c.outcome === 'error' ? '失败' : '未知'} ${c.count} 次`).join('，')}（计费以智慧芽账单为准）`}
              </Typography.Text>
            )}
          </Space>
        </Card>

        <Card title="人工意见（按市场）">
          <Row gutter={[16, 16]}>
            {data.markets.map((m) => (
              <Col xs={24} lg={12} key={m}>
                <ReviewPanel
                  checkId={data.id}
                  market={m}
                  marketName={marketName(m)}
                  reviews={data.reviews[m] ?? []}
                  candidates={data.candidates}
                  searchCompleted={data.search_completed}
                  canReview={!!access.canReviewRisk}
                  onSaved={() => load()}
                />
              </Col>
            ))}
          </Row>
        </Card>
      </Space>
    </PageContainer>
  );
}
