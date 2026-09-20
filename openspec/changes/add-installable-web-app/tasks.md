## 1. Manifest 与图标

- [x] 1.1 `scripts/make_pwa_icons.py` 由 `src/assets/logo-icon.png` 合成 192 / 512 / maskable-512 / apple-180 到 `frontend/public/`（脚本与产物入库；需换素材时 `uv run --with pillow python scripts/make_pwa_icons.py`）
- [x] 1.2 `public/manifest.webmanifest`：`id` `name` `short_name` `start_url` `scope` `display` `theme_color` `background_color` `icons`，start_url/scope/图标用相对路径
- [x] 1.3 `.umirc.ts` 用 `links`/`metas` 引入 manifest、apple-touch-icon、theme-color（href 以 `/` 开头）
- [x] 1.4 `src/manifest.test.ts`：字段断言、图标像素尺寸、`<link>` 写法、仓库无 Service Worker（**已写，尚未执行**）

## 2. 安装入口

- [x] 2.1 `src/utils/pwaInstall.ts`：模块加载时监听 `beforeinstallprompt`/`appinstalled`；`resolveInstallMode`、`isStandalone`、`isSafariWithAddToDock`（≥17）为纯函数
- [x] 2.2 `app.tsx` 顶部副作用 import，保证监听早于组件挂载
- [x] 2.3 `components/RightContent` 头像下拉「安装海眸系统」：`prompt` 调浏览器确认（用过即作废）、`safari-guide` 弹「添加到程序坞」指引、其余不显示
- [x] 2.4 `pwaInstall.test.ts` 与 `RightContent` 入口显隐用例（**已写，尚未执行**）

## 3. 部署

- [x] 3.1 `etc/keywordiq-v2-nginx.conf`：`index.html` 与 manifest 的 `Cache-Control: no-cache`，声明 `.webmanifest` MIME
- [ ] 3.2 PR 说明写明 ECS 上生效的 nginx 配置需**人工同步**并 `nginx -t && reload`，同步前后各 `curl -I` 核对两个响应头

## 4. 失败反馈核实

- [x] 4.1 核实结论：分析查询屏有两处缺口——首次查询失败时显示「搜索结果：0」+ 空表格；后续查询失败时表格留着旧条件的行、「有未生效改动」不亮。词库、导入历史等其余屏本轮**未逐屏核实**，验收时真机断网走一遍
- [x] 4.2 补最小缺口：`useAnalysisQuery.requestFailed`（只认最新发出的请求、下次成功即清、不进会话快照），失败期间不显示条数、显示常驻失败提示；`onRequestError` 空回调避免 ProTable 二次抛错；`requestFailure.test.tsx` 回归（**已写，尚未执行**）。不新增全局横幅与轮询；`errorHandler` 文案未改

## 5. 人工验证（需真机，本仓库无法自动化）

- [ ] 5.1 Windows 稳定版 Chrome、Edge：安装、独立窗口启动、图标与名称、深链接、刷新
- [ ] 5.2 Mac Safari（≥17）：「添加到程序坞」启动；指引文案与实际菜单一致
- [ ] 5.3 独立窗口内钉钉扫码登录全流程（跳转、回调、落点、无循环）；登录失效、退出、顶号提示
- [ ] 5.4 独立窗口内上传与导出下载
- [ ] 5.5 断网与服务端不可达的实际反馈（含分析查询屏首次失败与后续失败两种）
- [ ] 5.6 发布一个新前端版本后联网重新加载能拿到新版本
- [ ] 5.7 记录实测的系统与浏览器版本，未测平台在 PR 中明确标注
