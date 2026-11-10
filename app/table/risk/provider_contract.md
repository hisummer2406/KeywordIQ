# 智慧芽 Data REST API 精简契约

2026-11-10 按官方文档核对（open.zhihuiya.com/devportal：guides/authentication、api-reference 的
image-upload、image-single、simple-bibliography、legal-status）。只记本期用到的部分；以官方最新文档为准。
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

## 已知冲突（待真实联调核实）

- P060 的 `country`：类型一栏标为数组，说明里却写逗号分隔。适配器目前按数组发送。
- P060 返回的附图地址：正文说 9 小时过期，样例 URL 却带 `X-Amz-Expires=604800`（7 天）。系统按 9 小时提示。
- EU（EUIPO）与 WO（海牙）在 `country` 中的取值：目前用 `EM`、`WO`，界面标「待核实」。

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
