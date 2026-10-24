#!/usr/bin/env bash
# 采集 V2 生产 /api/analysis/search 的完整请求耗时分布与相关数据规模（只读）。
#
# 用途：#148 修复前基线与部署后重采（性能预算口径见
# openspec/changes/archive/2026-10-24-post-import-query-readiness/design.md「生产基线」「性能预算」），
# 以及 #154 的优化前后对比。口径：应用日志「请求摘要」的 duration_ms（完整请求，含鉴权与序列化）。
# V2 的 nginx 用默认 combined 格式，不记耗时，所以不从 nginx 取。
#
# 只读：只读应用日志；SQL 在只读会话里执行（default_transaction_read_only + statement_timeout）。
# 不改文件/配置/数据，不重启，不碰 V1。输出只含聚合数字与白名单筛选摘要，不含检索词、ASIN 或凭证。
#
# 用法（在本机执行，脚本经 stdin 传到 ECS；访问方式与授权每次另行确认）：
#   ssh <user>@<host> 'bash -s' < scripts/collect_search_latency.sh > baseline.txt
#   # 只统计某天（含）之后，例如部署之后：
#   ssh <user>@<host> 'SINCE=2026-11-01 bash -s' < scripts/collect_search_latency.sh
#
# 可选环境变量：
#   SINCE            只统计日志日期 >= 该日（YYYY-MM-DD），默认全部保留的日志（约 7 天）
#   KIQ_DIR          部署目录，默认 /var/www/keywordiq
#   COMPOSE_PROJECT  Compose 项目名，默认 keywordiq-v2
set -u
KIQ_DIR=${KIQ_DIR:-/var/www/keywordiq}
COMPOSE_PROJECT=${COMPOSE_PROJECT:-keywordiq-v2}
export SINCE=${SINCE:-}
cd "$KIQ_DIR" || { echo "找不到 $KIQ_DIR"; exit 1; }

echo "== 0. 采集时间与日志范围"
date -u +%FT%TZ
echo "SINCE=${SINCE:-（全部）}"
# 轮转文件名是 app.log-YYYYMMDD(.gz)，当天是 app.log；zgrep 对明文与 gz 一并处理。
ls -la logs/ 2>/dev/null | grep -E 'app\.log' || echo "logs/ 下没有 app.log*"
LOGS=$(ls logs/app.log* 2>/dev/null)

# 共用的分位数统计：stdin 每行「日期 毫秒」，按日 + 合计输出，并列出超过各预算阈值的比例。
read -r -d '' PCT_PY <<'PY'
import os, sys, collections
since = os.environ.get("SINCE") or ""
by = collections.defaultdict(list)
for line in sys.stdin:
    day, ms = line.split()
    if day >= since:
        by[day].append(int(ms))
def pct(v, p):
    v = sorted(v); return v[min(len(v) - 1, int(round(p / 100.0 * (len(v) - 1))))]
allv = [x for v in by.values() for x in v]
print("day n p50 p90 p95 p99 max (ms)")
for d in sorted(by):
    v = by[d]; print(d, len(v), *[pct(v, p) for p in (50, 90, 95, 99)], max(v))
if allv:
    print("ALL", len(allv), *[pct(allv, p) for p in (50, 90, 95, 99)], max(allv))
    for t in (1000, 3000, 5000, 25000, 30000):
        n = sum(x > t for x in allv)
        print("  >%dms: %d (%.1f%%)" % (t, n, 100.0 * n / len(allv)))
else:
    print("无记录")
PY

# 从请求摘要提取「日期 毫秒」；第二个参数是额外输出的字段（status / outcome 计数）。
summary() {
  zgrep -h "请求摘要 $1" $LOGS 2>/dev/null \
    | sed -nE 's/^([0-9]{4}-[0-9]{2}-[0-9]{2}).* status=([^ ]+) duration_ms=([0-9]+) outcome=([^ ]+).*/\1 \3 \2 \4/p'
}

echo
echo "== 1. POST /api/analysis/search 完整请求耗时（预算：日 p95≤3s、周 p95≤5s、单请求≤25s）"
echo "   注：请求摘要不带视图，此处为日周合计；视图拆分见第 3 节慢诊断。"
summary "POST /api/analysis/search " | awk '{print $1, $2}' | python3 -c "$PCT_PY"
summary "POST /api/analysis/search " | python3 -c '
import os, sys, collections
since = os.environ.get("SINCE") or ""
st = collections.Counter(); oc = collections.Counter()
for line in sys.stdin:
    day, _ms, status, outcome = line.split()
    if day >= since:
        st[status] += 1; oc[outcome] += 1
print("status", dict(st)); print("outcome", dict(oc))
'

echo
echo "== 2. 轻量接口对照（建议预算：p99≤1s，分析查询拥堵时不退化）"
echo "   列：ALL n p50 p90 p95 p99 max (ms)"
for p in "GET /api/auth/me" "GET /api/analysis/scope" "GET /health"; do
  echo "-- $p"
  summary "$p" | awk '{print $1, $2}' | python3 -c "$PCT_PY" | grep -E '^(ALL|无记录)'
done

echo
echo "== 3. 分析查询慢请求诊断（超过 ANALYSIS_SLOW_QUERY_THRESHOLD_MS 才记录；只取白名单字段）"
zgrep -h "分析查询慢请求诊断" $LOGS 2>/dev/null | python3 -c '
import os, re, sys, collections
since = os.environ.get("SINCE") or ""
rows = []
for line in sys.stdin:
    d = re.search(r"\d{4}-\d{2}-\d{2}", line)
    day = d.group(0) if d else "?"
    if day < since:
        continue
    g = lambda k: (re.search(k + r"=(\S+)", line) or [None, "?"])[1]
    tot = re.search(r"total_ms=(\d+)", line)
    flt = re.search(r"filters=(\{.*?\})", line)
    stg = re.search(r"stage_ms=(\{.*?\})", line)
    rows.append((int(tot.group(1)) if tot else -1, day, g("view"), g("on"),
                 g("request_failed"), g("request_cancelled"),
                 flt.group(1) if flt else "?", stg.group(1) if stg else "?"))
print("条数", len(rows))
print("按视图", dict(collections.Counter(r[2] for r in rows)))
print("失败/取消", dict(collections.Counter((r[4], r[5]) for r in rows)))
print("-- 最慢 25 条：total_ms day view on failed cancelled filters stage_ms")
for r in sorted(rows, reverse=True)[:25]:
    print(*r)
'

echo
echo "== 4. 数据规模与统计状态（只读 SQL）"
docker compose -p "$COMPOSE_PROJECT" exec -T db sh -c 'psql -X -v ON_ERROR_STOP=0 -U postgres -d "$POSTGRES_DB"' <<'SQL'
SET default_transaction_read_only = on;
SET statement_timeout = '20s';
\echo -- 4.1 各站点日/周期次数与每期行数
SELECT site, period_type, count(*) AS periods, min(report_date), max(report_date),
       round(avg(row_count)) AS avg_rows, max(row_count) AS max_rows, round(avg(dropped_count)) AS avg_dropped
  FROM analysis.site_data_stats GROUP BY 1, 2 ORDER BY 1, 2;
\echo -- 4.2 事实叶子分区规模与统计新鲜度
SELECT relname, n_live_tup, n_mod_since_analyze, last_analyze, last_autoanalyze,
       pg_size_pretty(pg_total_relation_size(relid)) AS size
  FROM pg_stat_user_tables
 WHERE schemaname = 'analysis' AND relname ~ '^(fact_daily|fact_weekly|fact_dropped|fact_growth)_'
 ORDER BY relname;
\echo -- 4.3 维度规模
SELECT (SELECT count(*) FROM analysis.dim_keyword) AS dim_keyword;
\echo -- 4.4 导入批次状态（不含文件名）
SELECT status, count(*) FROM analysis.import_batch GROUP BY 1 ORDER BY 1;
SELECT id, site, period_type, report_date, created_at FROM analysis.import_batch
 WHERE status = 'PROCESSING' ORDER BY id;
-- stats_status 由迁移 8f2a6c4e1b93 加入；部署前的库没有这一列，跳过而不报错。
SELECT EXISTS (
  SELECT 1 FROM information_schema.columns
   WHERE table_schema = 'analysis' AND table_name = 'import_batch' AND column_name = 'stats_status'
) AS has_stats_status \gset
\if :has_stats_status
\echo -- 4.5 已完成批次的查询统计状态（PENDING/FAILED 表示统计刷新未成功）
SELECT stats_status, count(*), max(stats_attempts) AS max_attempts
  FROM analysis.import_batch WHERE status = 'COMPLETED' GROUP BY 1 ORDER BY 1;
\else
\echo -- 4.5 跳过：import_batch 还没有 stats_status 列（#148 修复未部署）
\endif
\echo -- 4.6 规划器相关参数
SELECT name, setting, unit FROM pg_settings
 WHERE name IN ('max_connections', 'shared_buffers', 'effective_cache_size', 'work_mem',
                'random_page_cost', 'default_statistics_target',
                'autovacuum_analyze_scale_factor', 'autovacuum_analyze_threshold')
 ORDER BY name;
SQL
echo "== 完成"
