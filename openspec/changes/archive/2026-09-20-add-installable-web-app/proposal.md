## Why

用户希望从桌面、开始菜单或程序坞一键打开系统，不必每次输入网址（issue #92）。这是**访问入口优化**，
不是原生客户端：复用现有 SPA、登录与后端，必须联网使用，不做离线。

## What Changes

- 前端新增 `public/`：Web App Manifest 与图标（192 / 512 / maskable / 180 Apple），
  由 Umi 原样拷进 `dist/` 根，并在 HTML `<head>` 引用。
- 用户菜单（右上角头像下拉）新增「安装桌面端」入口：浏览器给出 `beforeinstallprompt`
  时由用户点击触发；Mac Safari 无程序化入口，给「添加到程序坞」操作指引；独立窗口内与
  不支持的环境不显示。
- nginx 模板给 `index.html` 与 Manifest 加 `Cache-Control: no-cache`，并显式声明
  `.webmanifest` 的 MIME，避免安装形态下被旧入口页锁住。
- 核实（必要时补最小缺口）主要屏请求失败时不会显示成成功或空数据。
- 新增 `manifest.test.ts`：守住 Manifest 字段、图标文件真实存在、以及「仓库里没有
  Service Worker」。

**不做**：Service Worker、Cache Storage、离线页、离线队列、推送、后台同步、原生壳；
不新增业务 API 或数据库表；不为「必须联网」禁用浏览器的正常 HTTP 缓存。

## Capabilities

### New Capabilities
- `installable-web-app`：系统可被浏览器安装为独立窗口的网站应用，且不引入任何离线能力。

### Modified Capabilities

- 无。钉钉 SSO 与会话唯一沿用现有行为，本变更只验证它们在独立窗口下成立，不改动其契约。

## Impact

- 前端：新增 `frontend/public/*`、`frontend/.umirc.ts`（`links`/`metas`）、
  `src/utils/pwaInstall.ts`、`src/components/RightContent` 一个菜单项。
- 部署：`etc/keywordiq-v2-nginx.conf` 是模板，**ECS 上生效的 nginx 配置要人工同步**，
  `deploy.sh` 不会自动应用；不同步的后果是 Manifest 可用但缓存头缺失。
- 后端、数据库、迁移：无。
- 无法在本仓库自动化验证的部分（各平台真机安装、独立窗口内钉钉扫码）由人工验证，
  实测的系统与浏览器版本记入 PR，未测的平台明确标注。
