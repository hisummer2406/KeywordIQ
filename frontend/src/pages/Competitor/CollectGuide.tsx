import { Typography } from 'antd';

/**
 * Amazon 手动采集的步骤说明（#166）。与 `docs/extension-install.md`「三、采集与上报」同一套
 * 步骤；插件安装包仍由管理员发放，界面里只写文字步骤，不做下载入口。
 *
 * **两条风险必须写在这里，不能省**：
 * - 分多次提交会让第一次单独成为基线，之后的存量商品被误判成「新发现」；
 * - 手动翻页同样可能触发站点的访问限制——不能把手动采集说成没有风控风险。
 */
export default function CollectGuide() {
  return (
    <div style={{ maxWidth: 360 }}>
      <ol style={{ paddingLeft: 18, margin: 0 }}>
        <li>点「打开商品列表」进入这个卖家的 Amazon 商品列表页（需已安装并登录 KeywordIQ 采集插件）</li>
        <li>在页面右上角的插件面板点「采集本页」</li>
        <li>有多页就逐页翻、每页都点一次「采集本页」</li>
        <li>全部采完后点一次「提交上报」</li>
      </ol>
      <Typography.Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0, fontSize: 12 }}>
        必须采完再提交一次：分次提交会让首批单独成为基线，之后采到的老商品被误判成新发现。
        数据只随你的采集更新，后台不会自动检查；频繁翻页也可能被 Amazon 限制访问。
        插件超过单次上限时会提前提示，不会截断提交。
      </Typography.Paragraph>
    </div>
  );
}
