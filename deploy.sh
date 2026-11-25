#!/bin/bash
# deploy.sh - KeywordIQ 部署脚本
#
# 部署形态：应用 + 自建 PostgreSQL 由 Docker Compose 管理（见 docker-compose.yml），
# host nginx 托管前端 SPA 产物并反代 /api 到应用容器（127.0.0.1:8001），配置见
# etc/keywordiq-v2-nginx.conf。曾经历过两次形态变化：docker-compose 路线于 9c0a31d 退役
# （理由：简化部署），host nginx + systemd 裸 uvicorn 路线又于
# dockerize-selfhosted-infra 变更退役（理由：自建 PostgreSQL 与同机部署需要容器编排
# 提供的重启监督/生命周期管理，两次的驱动因素并不相同）。
#
# 迁移不再在宿主机跑：`db` 服务不对宿主机暴露端口（design.md 决策 2），host 上的
# `alembic`/`uv` 够不到它，必须在 app 容器内执行（第 3 步用新镜像 `run --rm`，早于
# `up -d` 把旧容器换成新镜像——避免用旧代码接一份已经迁移过的新 schema）。
#
# 每次部署都会安装/更新 etc/keywordiq-logrotate.conf 到 /etc/logrotate.d/keywordiq
# （第 6 步，替换掉模板里的占位路径为真实绝对路径）——应用侧只用 WatchedFileHandler
# 检测文件被替换后重新打开，不做轮转，轮转的权威在宿主机 logrotate，见
# openspec/changes/add-request-diagnostics-logging/design.md 决策 A。
#
# 用法：
#   ./deploy.sh                  # 全量：构建镜像 + 迁移 + 前端构建 + 发布 + reload nginx
#   ./deploy.sh --skip-frontend  # 只更新后端（镜像 + 迁移）
#   ./deploy.sh --skip-migrate   # 不跑数据库迁移
#   ./deploy.sh --yes            # 不交互确认迁移（供 CI / 无人值守）
#   ./deploy.sh --ops-lock-held  # 调用方已持有运维动作锁（仅飞书执行器用），本脚本不再自取
#
# 发布全程持有运维动作锁（0x4B4F，与导入认领、飞书执行器同一把），持锁后复核没有
# PROCESSING/UPLOADING 的导入才继续；拿不到锁或有进行中导入直接失败，不重试
# （#209 P1，实现见 scripts/deploy_ops_lock.sh）。
#
# 可用环境变量覆盖：
#   WEB_ROOT      SPA 产物发布目录（默认 /var/www/keywordiq-web，须与 nginx root 一致）
#   SERVICE_NAME  旧路线（host nginx + systemd 裸 uvicorn）遗留的服务名（默认 keywordiq-v2），
#                 仅用于本次切容器化时清理该 unit——旧路线本身支持这个覆盖，若操作者
#                 当初把它改成了非默认名字，硬编码 keywordiq.service 找不到那个 unit，
#                 它会继续占着 127.0.0.1:8000（Codex 审核 PR #84 第十三轮）

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

WEB_ROOT="${WEB_ROOT:-/var/www/keywordiq-web}"
SERVICE_NAME="${SERVICE_NAME:-keywordiq-v2}"
APP_HEALTH_URL="${APP_HEALTH_URL:-http://127.0.0.1:8001/health}"
LOGROTATE_CONF_DST="${LOGROTATE_CONF_DST:-/etc/logrotate.d/keywordiq-v2}"

SKIP_FRONTEND=0
SKIP_MIGRATE=0
ASSUME_YES=0
OPS_LOCK_HELD=0

for arg in "$@"; do
    case "$arg" in
        --skip-frontend) SKIP_FRONTEND=1 ;;
        --skip-migrate)  SKIP_MIGRATE=1 ;;
        --yes|-y)        ASSUME_YES=1 ;;
        --ops-lock-held) OPS_LOCK_HELD=1 ;;
        # 打印开头的注释块（第 2 行到第一个空行），不写死行号
        --help|-h)       sed -n '2,/^$/p' "$0"; exit 0 ;;
        *) echo -e "${RED}未知参数: $arg${NC}（--help 查看用法）"; exit 2 ;;
    esac
done

cd "$(dirname "$0")"

info()  { echo -e "${GREEN}$*${NC}"; }
warn()  { echo -e "${YELLOW}$*${NC}"; }
fail()  { echo -e "${RED}$*${NC}" >&2; exit 1; }

# 写目标目录可能需要提权；能直接写就不用 sudo
as_root() {
    if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}KeywordIQ 部署（Docker Compose + host nginx）${NC}"
echo -e "${GREEN}======================================${NC}"

# ---------- 1. 前置检查 ----------
info "1/7 前置检查..."

[ -f main.py ]            || fail "错误: 未在项目根目录执行（找不到 main.py）"
[ -f .env ]                || fail "错误: .env 不存在。先 cp .env.example .env 并按注释填写"
[ -f docker-compose.yml ] || fail "错误: docker-compose.yml 不存在"

need() { command -v "$1" >/dev/null 2>&1 || fail "错误: 缺少命令 $1（$2）"; }
need docker "https://docs.docker.com/engine/install/"
docker compose version >/dev/null 2>&1 || fail "错误: docker compose 插件不可用"
need nginx "宿主机需已安装 nginx（前端 SPA 由它托管、/api 反代到应用容器）"
if [ "$SKIP_FRONTEND" -eq 0 ]; then
    need pnpm "npm i -g pnpm，或 corepack enable"
    need rsync "用于发布 SPA 产物"
fi
# WatchedFileHandler（main.py::configure_logging）本身不做轮转，logrotate 是唯一的
# 轮转权威。此前这里只是警告、放行部署——一次正常的容器化部署因此可能悄悄跑在一台
# 没有 logrotate 的机器上，logs/app.log 无限增长直到打满磁盘，且部署本身不会失败
# （Codex 复审 PR #98 P1：第一轮修复只处理了"配置文件没装"，没处理"命令本身缺失"这个
# 更基础的前提）。升级为硬性前置条件，与 docker/nginx 同等对待。
need logrotate "宿主机需已安装 logrotate（多数发行版随基础安装自带，缺失时用发行版包管理器安装，如 yum install logrotate）"
# 命令存在只说明能装配置，不说明有人定期跑它——logrotate 本身不是常驻进程，缺少定时
# 触发（cron.daily 或对应的 systemd timer）时装了配置也不会生效，和完全没装 logrotate
# 是同一个后果（磁盘无限增长），只是更隐蔽：`logrotate -d` 语法校验会通过，看起来
# 一切正常。RHEL/CentOS/Alibaba Cloud Linux 系发行版走 /etc/cron.daily/logrotate，
# 较新的发行版可能改用 systemd 的 logrotate.timer，两种都认。
#
# 光看文件/unit 是否存在不够：`-f` 只说明钩子脚本躺在那，不说明 cron 守护进程本身在跑
# （可能被停掉）；`systemctl is-enabled` 只说明开机会自启，不说明此刻是 active（可能
# enabled 但从未成功启动，或启动后又失败退出）。两种都是"装了触发机制却没人真的在跑"，
# 后果与完全没装相同，只是更隐蔽（Codex 复审 PR #98 第三轮 P1）。必须额外确认对应的
# 守护进程/定时器当前处于 active 状态。crond 在不同发行版下的服务名不同
# （RHEL 系是 crond，Debian 系是 cron），两个都探测。
logrotate_scheduler_running() {
    if [ -x /etc/cron.daily/logrotate ]; then
        systemctl is-active --quiet crond 2>/dev/null && return 0
        systemctl is-active --quiet cron 2>/dev/null && return 0
    fi
    systemctl is-active --quiet logrotate.timer 2>/dev/null && return 0
    return 1
}
logrotate_scheduler_running || fail "错误: logrotate 的触发机制未在实际运行——/etc/cron.daily/logrotate 存在但 crond/cron 服务未激活，且 systemd 的 logrotate.timer 也未激活。请先执行 systemctl start crond（或 cron / logrotate.timer）再重新部署"

echo "  WEB_ROOT = $WEB_ROOT"

# ---------- 运维动作锁：与导入互斥，持有到脚本退出 ----------
# 在改动任何生产状态（迁移、前端产物、容器）之前拿到；trap 保证任何退出路径都释放。
# --ops-lock-held 只给飞书执行器用：它已在自己的连接上持有同一把锁并在持锁期间复核过
# 导入状态（ops_bot/release/flow.py），这里再取会被自己挡住。
if [ "$OPS_LOCK_HELD" -eq 1 ]; then
    warn "调用方声明已持有运维动作锁（--ops-lock-held），跳过自取"
else
    info "取得运维动作锁并复核导入状态..."
    # shellcheck source=scripts/deploy_ops_lock.sh
    . scripts/deploy_ops_lock.sh
    trap ops_lock_release EXIT
    docker compose up -d db >/dev/null
    for i in $(seq 1 30); do
        docker compose exec -T db pg_isready -U postgres -q >/dev/null 2>&1 && break
        [ "$i" -eq 30 ] && fail "错误: 数据库 60 秒内未就绪，无法取得运维动作锁"
        sleep 2
    done
    ops_lock_acquire || fail "错误: 发布已中止，生产状态未改动"
    echo "  已持锁，无进行中导入；导入认领将等待本次发布结束"
fi

# ---------- 2. 构建应用镜像 ----------
info "2/7 构建应用镜像（docker compose build）..."
docker compose build app

# ---------- 3. 数据库迁移 ----------
if [ "$SKIP_MIGRATE" -eq 1 ]; then
    warn "3/7 跳过数据库迁移（--skip-migrate）"
else
    info "3/7 数据库迁移..."
    # db 服务不暴露端口给宿主机，迁移必须在容器内执行；先起 db（若还没起）再迁移。
    docker compose up -d db
    echo "  当前版本："
    docker compose run --rm app alembic current 2>&1 | sed 's/^/    /'
    echo "  目标版本（新镜像）："
    docker compose run --rm app alembic heads 2>&1 | sed 's/^/    /'

    if [ "$ASSUME_YES" -eq 0 ]; then
        # 迁移会改动生产库结构，默认要求人工确认
        read -r -p "  确认对该库执行 alembic upgrade head? [y/N] " reply
        case "$reply" in
            [yY]|[yY][eE][sS]) ;;
            *) fail "已取消。如只想发布前端，用 ./deploy.sh --skip-migrate" ;;
        esac
    fi
    docker compose run --rm app alembic upgrade head
fi

# ---------- 4. 前端构建 ----------
if [ "$SKIP_FRONTEND" -eq 1 ]; then
    warn "4/7 跳过前端构建（--skip-frontend）"
    warn "5/7 跳过产物发布（--skip-frontend）"
else
    info "4/7 构建前端 SPA..."
    (cd frontend && pnpm install --frozen-lockfile && pnpm build)
    [ -f frontend/dist/index.html ] || fail "错误: 构建未产出 frontend/dist/index.html"

    # ---------- 5. 发布产物 ----------
    info "5/7 发布产物到 $WEB_ROOT ..."
    as_root mkdir -p "$WEB_ROOT"
    # --delete 清掉上一版的哈希文件；结尾斜杠保证同步的是目录内容而非目录本身
    as_root rsync -a --delete frontend/dist/ "$WEB_ROOT/"
fi

# ---------- 6. 安装/更新 logrotate 配置 ----------
# WatchedFileHandler（main.py::configure_logging）本身不做轮转，轮转的权威在宿主机
# logrotate；etc/keywordiq-logrotate.conf 此前只是仓库里的模板，从没有任何自动化步骤
# 把它装进 /etc/logrotate.d/ ——一次正常的容器化部署因此会让 logs/app.log 无限增长
# 直到打满磁盘，且不会有任何报错（Codex 审核 PR #98 P1）。这里替换掉模板里的占位路径
# 为本次部署的真实绝对路径并安装，每次部署自动做，不再指望人工记得这一步。
info "6/7 安装/更新 logrotate 配置..."
LOGROTATE_CONF_SRC="etc/keywordiq-logrotate.conf"
REAL_LOG_PATH="$(pwd)/logs/app.log"
tmp_logrotate="$(mktemp)"
sed "s#/path/to/keywordiq/logs/app.log#${REAL_LOG_PATH}#" "$LOGROTATE_CONF_SRC" > "$tmp_logrotate"
if cmp -s "$tmp_logrotate" "$LOGROTATE_CONF_DST" 2>/dev/null; then
    echo "  logrotate 配置未变化，跳过安装"
else
    as_root cp "$tmp_logrotate" "$LOGROTATE_CONF_DST"
    echo "  已安装 $LOGROTATE_CONF_DST（日志路径：$REAL_LOG_PATH）"
fi
rm -f "$tmp_logrotate"
# 空跑校验语法，配置错误比没配置更隐蔽——cron.daily 触发的失败通常没人第一时间看到。
# logrotate 命令本身与定时触发机制已在第 1 步确认存在，这里不再需要分支判断。
as_root logrotate -d "$LOGROTATE_CONF_DST" >/dev/null \
    || fail "错误: logrotate 配置校验未通过（sudo logrotate -d $LOGROTATE_CONF_DST 查看详情）"

# ---------- 7. 重载 nginx + 切换容器到新镜像 ----------
info "7/7 重载 nginx 并切换容器到新镜像..."
as_root nginx -t
as_root systemctl reload nginx
echo "  nginx 已重载"

# 首次由旧路线（host nginx + systemd 裸 uvicorn）切到本容器化路线时，遗留的
# ${SERVICE_NAME}.service 仍绑在 127.0.0.1:8000——与 app 容器要绑的端口相同，不先停掉它，
# 下面的 docker compose up -d 会因端口已被占用而起不来。从仓库里删掉
# etc/keywordiq.service 只影响下次全新安装，不会停掉宿主机上已安装、正在跑的 unit。
# 探测与操作都读 SERVICE_NAME 而非硬编码 keywordiq：旧路线本身支持这个环境变量覆盖，
# 操作者若当初把服务名改成了别的，硬编码会找不到那个 unit（Codex 审核 PR #84 第十三轮）。
# stop 与 disable 分别按 is-active / is-enabled 独立判断，不能只查 is-active：
# unit 若被手工停过、处于 failed，或本次部署前恰好还在 activating，is-active 为假，
# 但只要它仍是 enabled，宿主机下次重启时 systemd 照样把它拉起来，回头跟新容器抢
# 127.0.0.1:8000（Codex 审核 PR #84 第六轮）。两个判断各自天然幂等：停过之后
# is-active 恒为假，disable 过之后 is-enabled 恒为假，不需要额外记状态。
#
# disable 必须带 --now，不能是纯 disable：`systemctl is-active` 对 "activating"
# 状态返回非零（只有 "active"/"reloading" 算），若 unit 恰好在这次部署时正处于
# activating（比如宿主机刚重启、旧 unit 还在启动过程中），上面的 is-active 分支判定
# 为"未在运行"而跳过 stop；这种 unit 通常是 enabled 的，会落进下面这个分支，但纯
# `disable`（不带 --now）只挪走开机自启的符号链接，不会停掉当前正在 activating 的
# 进程——它仍可能在本脚本继续往下跑到 `docker compose up -d` 之前完成绑定
# 127.0.0.1:8000，和新容器抢端口（Codex 审核 PR #84 第十九轮）。`--now` 是
# "enable/disable 之后顺带 start/stop 一次"的标准选项，一步到位覆盖这条 stop 分支
# 覆盖不到的 activating 窗口。
if systemctl is-active --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
    warn "  检测到遗留 ${SERVICE_NAME}.service 仍在运行并占用 127.0.0.1:8000，先停止..."
    as_root systemctl stop "${SERVICE_NAME}.service"
fi
if systemctl is-enabled --quiet "${SERVICE_NAME}.service" 2>/dev/null; then
    warn "  检测到遗留 ${SERVICE_NAME}.service 仍处于启用状态，禁用并停止避免抢占端口..."
    as_root systemctl disable --now "${SERVICE_NAME}.service"
fi

# up -d 对已用新镜像 build 过的服务会重建容器；db/netdata 若已在跑且配置未变则不受影响。
docker compose up -d
echo "  容器已切换到新镜像（restart: unless-stopped，之后崩溃/宿主机重启由 Docker 自动拉起）"
if [ "$OPS_LOCK_HELD" -eq 0 ] && ! ops_lock_alive; then
    # 通常是 up -d 重建了 db 容器，持锁连接随之断开；此后导入可能已被认领。
    warn "  ⚠️  运维动作锁连接已中断（db 容器可能被重建），切换期间的导入互斥不再有保证"
fi

# ---------- 健康检查 ----------
info "健康检查..."
for i in $(seq 1 15); do
    if curl -fsS "$APP_HEALTH_URL" >/dev/null 2>&1; then
        info "✅ 部署完成，后端健康检查通过"
        exit 0  # EXIT trap 释放运维动作锁
    fi
    sleep 2
done

# 非零退出：新容器一直没就绪，nginx 前面几步已经重载完毕，此时 nginx 正在把流量转给
# 一个还没通过健康检查的后端。静默 exit 0 会让无人值守调用（--yes，CI/自动发布）把这次
# 部署记成成功，而实际服务可能不可用——只打印警告不够，退出码必须让上游据此判定失败
# 并触发告警（Codex 审核 PR #84 第六轮）。
warn "⚠️  部署动作已完成，但 15 次重试内 /health 未通过。"
warn "   排查：docker compose logs app --tail 100"
exit 1
