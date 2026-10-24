import deFlag from '@/assets/flags/de.svg';
import gbFlag from '@/assets/flags/gb.svg';
import usFlag from '@/assets/flags/us.svg';
import type { SiteOption } from '@/services/upload';
import './siteLabel.less';

/**
 * 国旗用随包的 SVG，不用 Emoji 区域指示符：Windows 的默认字体不画国旗，
 * Chrome/Edge 会把 🇺🇸 显示成两个字母（#151）。
 *
 * 只登记已开通的站点。未登记的站点不出国旗、只显示文字——国旗是装饰，
 * 站点名和站点码才是识别依据，所以缺图不影响辨认。
 */
const FLAG_SRC: Record<string, string> = {
  US: usFlag,
  GB: gbFlag,
  DE: deFlag,
};

function normalizeCode(code: string): string {
  return code.trim().toUpperCase();
}

export function siteFlagSrc(code: string): string | undefined {
  return FLAG_SRC[normalizeCode(code)];
}

/** 装饰性国旗：旁边总有站点文字，`alt=""` 让屏幕阅读器跳过，避免重复朗读。 */
export function SiteFlag({ code }: { code: string }) {
  const src = siteFlagSrc(code);
  if (!src) return null;
  return <img className="site-flag" src={src} alt="" aria-hidden="true" />;
}

export function formatSiteLabel(site: SiteOption): string {
  return `${site.name_zh}站（${normalizeCode(site.code)}）`;
}

/** 国旗 + 站点文字，供下拉的选中值与选项共用，保证两处一致。 */
export function SiteLabel({ site }: { site: SiteOption }) {
  return (
    <span className="site-label">
      <SiteFlag code={site.code} />
      <span>{formatSiteLabel(site)}</span>
    </span>
  );
}

/** 表格等窄处：国旗 + 站点码。 */
export function SiteCodeLabel({ code }: { code: string }) {
  return (
    <span className="site-label">
      <SiteFlag code={code} />
      <span>{normalizeCode(code)}</span>
    </span>
  );
}

/** 站点下拉选项。`label` 成了节点，`title` 补回纯文本悬停提示。 */
export function toSiteSelectOptions(sites: SiteOption[]) {
  return sites.map((site) => ({
    label: <SiteLabel site={site} />,
    title: formatSiteLabel(site),
    value: site.code,
  }));
}
