## RENAMED Requirements

- FROM: `### Requirement: 供应商客户端固定源站并按契约调用四个接口`
- TO: `### Requirement: 供应商客户端固定源站并按契约调用接口`

## MODIFIED Requirements

### Requirement: 供应商客户端固定源站并按契约调用接口

客户端 SHALL 只请求 `https://connect.zhihuiya.com`，MUST NOT 接受任何来自用户或配置的源站地址。鉴权 SHALL 使用 `Authorization: Bearer <Key>` 请求头，MUST NOT 使用 `apikey` 查询参数。客户端 SHALL 按以下契约调用：

- P010 `POST /image-search/image-upload`，multipart 字段 `image`；
- P060 `POST /search/patent/image-single`，JSON 请求体，`patent_type=D`、`model=1`、`is_https=1`、`url`，以及用户确认的 `country`（数组）、`limit`、`offset`；
- P011 `GET /basic-patent-data/simple-bibliography` 与 P013 `GET /basic-patent-data/legal-status`，`patent_id` 逗号分隔，每次最多 100 个；
- P042 `POST /basic-patent-data/fulltext-image`，JSON 请求体 `patent_id`（一件）、`limit`、`offset`；
- P020 `GET /basic-patent-data/pdf-data`，`patent_id` 一件。

外部调用 MUST NOT 在事件循环中阻塞，也 MUST NOT 在等待期间持有数据库事务或连接。请求日志、HTTP 客户端日志和异常日志 MUST NOT 记录 `Authorization` 请求头或 Key。

#### Scenario: 模拟服务核对请求形状

- **WHEN** 测试用模拟 HTTP 服务接收真实客户端发出的六类请求
- **THEN** 能看到 Bearer 请求头、multipart 字段 `image`、P060 的 `patent_type=D` 与 `model=1`、P011/P013 的逗号分隔 `patent_id`、P042 的 JSON `patent_id`/`limit`/`offset`、P020 的查询参数 `patent_id`

## ADDED Requirements

### Requirement: 签名文件只从智慧芽文件域名下载

P042、P020 返回的签名链接 SHALL 只在主机名以 `.zhihuiya.com` 结尾且协议为 `https` 时下载；客户端 MUST NOT 跟随重定向，MUST NOT 使用环境变量中的代理，MUST NOT 携带智慧芽 API Key。下载 SHALL 流式读取并设上限（图片 20 MB、PDF 50 MB），超限即中止；内容格式 SHALL 按文件头判断，图片只接受 PNG、JPEG、GIF、WebP、TIFF，PDF 必须以 `%PDF-` 开头。下载不是计费调用，不写调用留痕；日志 MUST NOT 记录完整签名链接。

#### Scenario: 链接指向其他主机

- **WHEN** P042 返回的 `path` 是 `https://evil.example.com/a.png`
- **THEN** 不发起下载，该条目记为未存档，分类为 `untrusted_url`

#### Scenario: 内容不是 PDF

- **WHEN** P020 的链接返回一段 HTML
- **THEN** 该文件记为未存档，分类为 `bad_content`，详情状态仍为 `success`
