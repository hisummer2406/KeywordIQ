import { GoogleOutlined } from '@ant-design/icons';
import { Button, Drawer, Space, Typography } from 'antd';
import { useState } from 'react';
import { googleTrendsUrl } from './googleTrendsUrl';
import { siteCodeToFlag } from './siteLabel';

const { Text, Paragraph } = Typography;

/**
 * 行内「Google 搜索趋势」入口（issue #78 V0：站外需求验证证据）。
 *
 * 点击打开底部抽屉，抽屉里**只放查询摘要 + 跳转按钮**，不内嵌 Google 页面——实测
 * `trends.google.com` 的探索页与官方 embed widget 都带 `X-Frame-Options: SAMEORIGIN`，
 * 浏览器会直接拒绝跨域 iframe，这不是实现难度问题。真正的内嵌趋势图要等 V1 拿到正式
 * API 授权后才能做（见 issue #78「数据源决策」一节）。
 *
 * 拿不到站点码（`googleTrendsUrl` 返回 `null`）时整个入口不渲染——原则同关键词单元格
 * 本身域名缺失时的降级：宁可不给入口，也不要给一个点了打不开或打到错误地区的链接。
 */

interface Props {
  keyword: string;
  /** 生效作用域的站点码，直接当 Google Trends 的 geo 参数用，无需另外映射。 */
  siteCode?: string;
}

export default function GoogleTrendsButton({ keyword, siteCode }: Props) {
  const [open, setOpen] = useState(false);
  const url = googleTrendsUrl(keyword, siteCode);
  if (!url) return null;

  return (
    <>
      <Button
        type="text"
        size="small"
        icon={<GoogleOutlined />}
        title="查看该词在 Google 的站外搜索热度"
        aria-label="查看 Google 搜索趋势"
        // 阻止冒泡：这一列还有整行可点的场景（如未来的行选中），图标只应触发自己的抽屉
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        style={{ padding: '0 2px', flexShrink: 0 }}
      />
      <Drawer
        title="Google 搜索趋势（站外需求验证）"
        placement="bottom"
        height={240}
        open={open}
        onClose={() => setOpen(false)}
      >
        <Space direction="vertical" size="middle" style={{ width: '100%' }}>
          <Space size="large" wrap>
            <Text>
              关键词：<Text strong>{keyword}</Text>
            </Text>
            <Text>
              Google Trends 地区：{siteCodeToFlag(siteCode ?? '')} {siteCode}
            </Text>
            <Text>默认时间窗口：近 12 个月</Text>
          </Space>
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            数据来自 Google Trends 官方公开页面，反映的是<strong>相对搜索热度</strong>，
            <Text type="danger">不代表搜索量、销量或 Amazon 站内需求</Text>，
            仅供判断该概念在站外是否同步升温、有无季节性或地区差异。
            本产品不采集或存储 Google 数据，点击后在新标签页打开官方页面。
          </Paragraph>
          <Button
            type="primary"
            icon={<GoogleOutlined />}
            href={url}
            target="_blank"
            rel="noopener noreferrer"
          >
            在 Google Trends 中打开
          </Button>
        </Space>
      </Drawer>
    </>
  );
}
