---
paths:
  - "extension/**/*"
---

# 浏览器插件约束

- 同时读 competitor 规则、[插件 README](../../extension/README.md) 与 [采集规格](../../openspec/specs/competitor-browser-collector/spec.md)。
- manifest/package 版本一致；manifest 保留固定 ID 的公钥 key，私钥不入库。
- 多页先本地合并、一次请求上报；禁止逐页各报，避免错误建立基线。
- 跨域用 optional_host_permissions 随 API 地址申请；不依赖后端 CORS，不写死固定 API host_permissions。
- 同步网页 JWT 不等于独立可吊销凭证；不把现有全权限 token 描述成已收窄。
- 分发/打包/升级按 README 操作；DOM 选择器必须真实页面验证，旧真机记录不代表当前全部站点通过。
