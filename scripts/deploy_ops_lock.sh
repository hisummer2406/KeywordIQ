# shellcheck shell=bash
# deploy_ops_lock.sh —— deploy.sh 在整个发布期间持有运维动作锁（#209 P1）。
#
# 由 deploy.sh `source`，不单独执行。与飞书执行器（ops_bot/release/lock.py）、导入认领
# （app/table/upload/import_service.py::_claim_processing_slot）共用同一把咨询锁
# OPS_ACTION_LOCK_KEY = 0x4B4F：
#   - 导入先认领：批次已是 PROCESSING，持锁后的复核发现它 → 发布拒绝执行；
#   - 发布先拿锁：导入认领时抢不到这把锁 → 批次留在 QUEUED，发布结束后由轮询接手。
# 持锁前的检查只算快速失败；持锁后的复核才是互斥边界，与 ops_bot/release/flow.py 同理。
#
# 锁是会话级的，必须由一条一直开着的连接持有：后台起一个 psql，从 FIFO 读命令，
# deploy.sh 握着 FIFO 的写端。脚本退出（含 set -e 失败、被 kill）时写端关闭 →
# psql 读到 EOF 退出 → 连接断开 → 锁随之释放，不会残留。
# 只用 bash 3.2 也有的语法（FIFO + 固定 fd），不依赖 coproc。

OPS_ACTION_LOCK_KEY=19279  # 0x4B4F，必须与 import_service._OPS_ACTION_LOCK_KEY 同值
OPS_LOCK_FD=9
# 就绪名每次调用唯一：两次发布重叠时，后一次的探测不能把前一次的持锁会话认成自己的
# （同名时后者会在自己还没拿到锁时误报成功，继续迁移、切容器；PR #214 Codex P1）。
OPS_LOCK_PENDING_APP="keywordiq-deploy-lock"
OPS_LOCK_READY_APP=""
OPS_LOCK_PID=""
OPS_LOCK_ERR=""

# 在 db 容器内跑 psql（db 不对宿主机暴露迁移所需的连接，见 deploy.sh 第 3 步）。
# $1 为 application_name；SQL 从 stdin 读。测试里可重定义这个函数指向一次性库。
ops_lock_psql() {
    docker compose exec -T -e PGAPPNAME="$1" db \
        sh -c 'exec psql -X -q -At -v ON_ERROR_STOP=1 -U postgres -d "$POSTGRES_DB"'
}

# 成功返回 0 并保持持锁；失败返回非零，原因写到 stderr（锁连接已退出、不会残留）。
ops_lock_acquire() {
    local fifo i ready
    OPS_LOCK_READY_APP="keywordiq-deploy-lock-ready-$$-${RANDOM}${RANDOM}"
    fifo="$(mktemp -u "${TMPDIR:-/tmp}/keywordiq-ops-lock.XXXXXX")"
    OPS_LOCK_ERR="$(mktemp "${TMPDIR:-/tmp}/keywordiq-ops-lock-err.XXXXXX")"
    mkfifo "$fifo"
    ops_lock_psql "$OPS_LOCK_PENDING_APP" <"$fifo" >/dev/null 2>"$OPS_LOCK_ERR" &
    OPS_LOCK_PID=$!
    # 打开写端会阻塞到读端（上面的后台进程）打开为止；之后 FIFO 文件本身可以删掉。
    eval "exec ${OPS_LOCK_FD}>\"\$fifo\""
    rm -f "$fifo"

    # 拿锁与复核放在同一个 DO 块里：抢不到或有进行中导入都抛错，ON_ERROR_STOP 让 psql
    # 立刻退出、连接断开，已拿到的锁随之释放。全部通过后才改 application_name，
    # 下面的就绪探测只认这个名字，不会把"已拿锁、还没复核完"误判为就绪。
    printf '%s\n' \
        'DO $$ BEGIN' \
        "  IF NOT pg_try_advisory_lock(${OPS_ACTION_LOCK_KEY}) THEN" \
        "    RAISE EXCEPTION 'OPS_LOCK_BUSY: 运维动作锁被占用（另一个重启/发布正在执行，或导入正在认领批次），请稍后重新发布';" \
        '  END IF;' \
        "  IF EXISTS (SELECT 1 FROM analysis.import_batch WHERE status IN ('PROCESSING', 'UPLOADING')) THEN" \
        "    RAISE EXCEPTION 'IMPORT_ACTIVE: 存在进行中的导入（PROCESSING/UPLOADING），拒绝发布，等导入结束后重新发布';" \
        '  END IF;' \
        'END $$;' \
        "SET application_name = '${OPS_LOCK_READY_APP}';" >&"$OPS_LOCK_FD"

    for i in $(seq 1 30); do
        if ! kill -0 "$OPS_LOCK_PID" 2>/dev/null; then
            wait "$OPS_LOCK_PID" 2>/dev/null || true
            eval "exec ${OPS_LOCK_FD}>&-"
            echo "未能取得运维动作锁：$(grep -oE '(OPS_LOCK_BUSY|IMPORT_ACTIVE): .*' "$OPS_LOCK_ERR" | head -1 || true)" >&2
            [ -s "$OPS_LOCK_ERR" ] && sed 's/^/    /' "$OPS_LOCK_ERR" >&2
            rm -f "$OPS_LOCK_ERR"
            OPS_LOCK_PID=""
            return 1
        fi
        ready="$(printf '%s\n' \
            "SELECT count(*) FROM pg_stat_activity a JOIN pg_locks l ON l.pid = a.pid" \
            " WHERE a.application_name = '${OPS_LOCK_READY_APP}' AND l.locktype = 'advisory'" \
            "   AND l.granted AND l.classid = 0 AND l.objid = ${OPS_ACTION_LOCK_KEY} AND l.objsubid = 1;" \
            | ops_lock_psql "keywordiq-deploy-lock-probe" 2>/dev/null || true)"
        if [ "$ready" = "1" ]; then
            return 0
        fi
        sleep 1
    done
    ops_lock_release
    echo "未能取得运维动作锁：30 秒内未确认持锁，已放弃" >&2
    return 1
}

# 幂等：未持锁时什么也不做。关闭写端后等 psql 退出，确保返回时锁已释放。
ops_lock_release() {
    [ -n "$OPS_LOCK_PID" ] || return 0
    eval "exec ${OPS_LOCK_FD}>&-" 2>/dev/null || true
    wait "$OPS_LOCK_PID" 2>/dev/null || true
    OPS_LOCK_PID=""
    [ -n "$OPS_LOCK_ERR" ] && rm -f "$OPS_LOCK_ERR"
    OPS_LOCK_ERR=""
}

# 持锁的连接是否还活着（db 容器被重建等情况会让它断开，锁随之丢失）。
ops_lock_alive() {
    [ -n "$OPS_LOCK_PID" ] && kill -0 "$OPS_LOCK_PID" 2>/dev/null
}
