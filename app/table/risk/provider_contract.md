# 智慧芽 Data REST API 精简契约

2026-11-10 按官方文档核对（open.zhihuiya.com/devportal：guides/authentication、api-reference 的
image-upload、image-single、simple-bibliography、legal-status）；P042、P020 按 2026-11-11 真实联调补充
（change add-patent-fulltext-evidence）。只记用到的部分；以官方最新文档为准。
样例全部是脱敏构造的形状示意，不是真实响应。

## 公共约定

- 源站固定为 `https://connect.zhihuiya.com`；客户端 `trust_env=False`，不走环境变量代理。
- 鉴权方式：`Authorization: Bearer sk-...`。另有 `apikey` 查询参数方式，官方说明它会进 URL 日志，**不用**。
- 响应外层：`status`（bool）、`error_code`（int，0 表示成功）、`error_msg`、`data`。
- 官方标注的响应时间：正常 5000 ms，最大 10000 ms。文档没有给出 QPS 和价格。

## 接口

| 编号 | 方法 / 路径 | 请求 | 成功时的 `data` |
|---|---|---|---|
| P010 | POST `/image-search/image-upload` | multipart 字段 `image`，JPG/PNG，不超过 4096 KB | `{url, expire}`，`expire` 单位为秒，样例为 32400 |
| P060 | POST `/search/patent/image-single` | JSON：`url`、`patent_type=D`、`model=1`（智能联想）、`limit` 1–100（默认 10）、`offset` 0–1000、`field=SCORE`、`is_https=1`、`country` | `{total_search_result_count, patent_messages[]}`，每条含 `patent_id`、`patent_pn`、`title`、`url`、`img_id`、`score`、`apdt`、`pbdt`、`apno`、`authority`、`loc[]`、`current_assignee`、`original_assignee` |
| P011 | GET `/basic-patent-data/simple-bibliography` | `patent_id`，逗号分隔，最多 100 个，按成功条数计费 | `[{pn, patent_id, bibliographic_data{invention_title[], parties{assignees,applicants,inventors}, patent_type, application_reference, publication_reference, classification_data{classification_loc}, ...}}]` |
| P013 | GET `/basic-patent-data/legal-status` | 同 P011 | `[{pn, patent_id, legal_date, patent_legal{simple_legal_status[], legal_status[], event_status[]}}]` |
| P042 | POST `/basic-patent-data/fulltext-image` | JSON：`patent_id`（一件）、`limit`、`offset` | `{patent_id, pn, total_search_result_count（该件附图总数）, result_count, fulltext_image[{image_type, path}]}`；`path` 是 `data-fulltext-image.zhihuiya.com` 的签名链接，约 7 天过期 |
| P020 | GET `/basic-patent-data/pdf-data` | 查询参数 `patent_id`（本系统按件调用） | `[{patent_id, pn, pdf{path}}]`；`path` 是 `data-pdf.zhihuiya.com` 的签名链接，约 10 天过期 |

## 真实联调结论（2026-11-11）

- P060 的 `country` 必须传数组。逗号分隔字符串返回 `error_code=20000`（`Json mapping failed`）。
- EUIPO 的取值是 `EU`，海牙的取值是 `WO`。`EM` 不是有效取值：单独查询时 `status=true` 但 `data` 没有内容，与其他法域同查时被静默忽略。
- `total_search_result_count` 不是可相加的真实总量，多法域的总数可能小于单查某一法域。

- P042 JSON 请求体可用（`limit=5`、`offset=0` 实测）；样本 `USD976055S` 的 `total_search_result_count=8`、`image_type` 均为 `drawing`。
- 签名链接带 `response-expires`（HTTP 日期），系统据此记录链接过期时间。

## 未验证

- P042 `limit` 上限（系统按 20 翻页）、P020 是否支持逗号批量（系统按件调用）、P042/P020 的计费方式与 QPS。

## 已知冲突（待真实联调核实）

- P060 返回的附图地址：正文说 9 小时过期，样例 URL 却带 `X-Amz-Expires=604800`（7 天）。系统按 9 小时提示。

## 错误码分类（`provider_client.py`）

| 分类 | 错误码 / 条件 |
|---|---|
| credential_invalid | 67200003、67200008、67200009、HTTP 401 |
| no_permission | 67200004、HTTP 403 |
| insufficient_balance | 67200005 |
| client_expired | 67200006 |
| quota_exceeded | 67200007 |
| rate_limited | 67200000、67200001、67200002、HTTP 429 |
| bad_request | 67200012、68300004、68300007、68300010 |
| upstream_error | 67200100、67200101、68300005、68300006、68300008 |
| unexpected_code | 其他非 0 错误码 |
| client_error / connect_failed（error，请求未发出） | 本地构造客户端失败、连接阶段失败 |
| 结果未知（unknown） | 超时、网络中断、HTTP 5xx、返回非 JSON 或 HTML、`status=true` 但结构不符合契约 |

## 脱敏样例

```json
{"status": true, "error_code": 0, "data": {"url": "https://static-open.zhihuiya.com/tmp/xxxx.png", "expire": 32400}}
{"status": true, "error_code": 0, "data": {"total_search_result_count": 0, "patent_messages": []}}
{"status": true, "error_code": 0, "data": {"total_search_result_count": 100, "patent_messages": [
  {"patent_id": "00000000-0000-0000-0000-000000000000", "patent_pn": "USD000000S", "title": "Cup",
   "url": "https://.../x.png", "img_id": "x", "score": 0.93, "apdt": 20200101, "pbdt": 20210101,
   "authority": "US", "loc": ["07-01"], "current_assignee": "ACME"}]}}
{"status": false, "error_code": 67200005, "error_msg": "..."}
```
