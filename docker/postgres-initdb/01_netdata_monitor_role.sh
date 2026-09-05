#!/bin/bash
# Postgres 官方镜像的约定：只在容器首次初始化（空数据目录）时执行一次。
# 建一个只读监控角色供 Netdata 的 PostgreSQL 采集插件使用，不复用应用连接账号
# （见 tasks.md 8.2、design.md 决策 9）。pg_monitor 是 PostgreSQL 内置角色，只授予
# 只读的统计视图访问权限，不能读写业务数据。
#
# 密码写死而非走 .env：这个角色只在 Docker 内部网络内可达（db 不对外暴露端口），
# Netdata 静态 YAML 配置（docker/netdata/go.d/postgres.conf）本身也不支持 ${VAR}
# 替换，两处只能手工保持一致。要改密码，两个文件一起改。
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE ROLE netdata WITH LOGIN PASSWORD 'netdata_monitor_only';
    GRANT pg_monitor TO netdata;
    GRANT CONNECT ON DATABASE ${POSTGRES_DB} TO netdata;
EOSQL
