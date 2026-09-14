#!/bin/bash
# Postgres 官方镜像的约定：只在容器首次初始化（空数据目录）时执行一次。
# 建一个只读监控角色供 Netdata 的 PostgreSQL 采集插件使用，不复用应用连接账号
# （见 tasks.md 8.2、design.md 决策 9）。pg_monitor 是 PostgreSQL 内置角色，只授予
# 只读的统计视图访问权限，不能读写业务数据。
#
# 密码写死而非走 .env：这个角色只在 Docker 内部网络内可达（db 不对外暴露端口），
# Netdata 静态 YAML 配置（docker/netdata/go.d/postgres.conf）本身也不支持 ${VAR}
# 替换，两处只能手工保持一致。要改密码，两个文件一起改；要改 POSTGRES_DB 同理——
# 下面这条 GRANT CONNECT 会跟着 .env 自动生效，但 postgres.conf 里写死的库名不会，
# 漏改的表现是 Netdata 连一个不存在的库，PostgreSQL 那组监控图表静默消失。
set -euo pipefail

# POSTGRES_DB 直接以 bash 变量插进 heredoc（未加标识符引号）时，若库名含 `-` 之类
# 需要引号的字符（如 `keyword-iq`），拼出的是 `GRANT CONNECT ON DATABASE keyword-iq`
# ——PostgreSQL 会把它解析成 `keyword - iq` 减法表达式而语法错误；本脚本又跑在
# `ON_ERROR_STOP=1` 下，会让容器首次初始化直接中止（Codex 审核 PR #84 第八轮）。
# 改用 psql 变量 + `:"var"` 标识符引号语法，由 psql 负责按标识符规则转义，不再靠
# bash 插值裸拼 SQL。
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
    -v dbname="$POSTGRES_DB" <<-EOSQL
    CREATE ROLE netdata WITH LOGIN PASSWORD 'netdata_monitor_only';
    GRANT pg_monitor TO netdata;
    GRANT CONNECT ON DATABASE :"dbname" TO netdata;
EOSQL
