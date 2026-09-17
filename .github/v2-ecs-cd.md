# V2 ECS 持续部署

`Deploy V2 to ECS` 是 V2 的唯一 GitHub Actions 发布入口。仓库管理员在 `v2` 分支的
Actions 页面手动运行它；工作流会核验当前 SHA 的后端、前端和扩展 CI 均已成功，再连接
ECS 执行 `./deploy.sh --yes`。

部署脚本的 V2 默认值如下：

| 项目 | 值 |
| --- | --- |
| 应用健康检查 | `http://127.0.0.1:8001/health` |
| 前端发布目录 | `/var/www/keywordiq-web` |
| 旧 systemd 服务名 | `keywordiq-v2` |
| logrotate 配置 | `/etc/logrotate.d/keywordiq-v2` |

这些值使 V2 不会访问 V1 的 `127.0.0.1:8000`、`amazon.liangtong.online` 或 V1 的静态文件目录。

## 一次性引导 ECS

以下命令以 ECS 的 `www` 用户执行。先让部署账户可以调用 Docker；重新登录后，`docker ps`
应当无需 `sudo`。

```bash
sudo usermod -aG docker www
exit
```

重新以 `www` 登录后，创建部署脚本所需的受限 sudo 规则：

```bash
sudo visudo -f /etc/sudoers.d/keywordiq-v2-cd
```

写入：

```sudoers
www ALL=(root) NOPASSWD: \
  /usr/bin/mkdir -p /var/www/keywordiq-web, \
  /usr/bin/rsync -a --delete frontend/dist/ /var/www/keywordiq-web/, \
  /usr/bin/cp * /etc/logrotate.d/keywordiq-v2, \
  /usr/sbin/logrotate -d /etc/logrotate.d/keywordiq-v2, \
  /usr/sbin/nginx -t, \
  /usr/bin/systemctl reload nginx
```

`deploy.sh` 还会在首次部署时安装 V2 的 logrotate 配置。执行一次：

```bash
cd /var/www/keywordiq
tmp_logrotate="$(mktemp)"
sed "s#/path/to/keywordiq/logs/app.log#$(pwd)/logs/app.log#" \
  etc/keywordiq-logrotate.conf > "$tmp_logrotate"
sudo install -o root -g root -m 0644 "$tmp_logrotate" /etc/logrotate.d/keywordiq-v2
sudo logrotate -d /etc/logrotate.d/keywordiq-v2
rm -f "$tmp_logrotate"

# 必须以 www 身份运行；不要加 sudo。
./deploy.sh --yes
```

`./deploy.sh` 之前的命令只安装 root 拥有的 logrotate 配置；最后一行以 `www` 构建前端，确保
`frontend/node_modules` 和 `frontend/dist` 不会变成 root 所有。之后 Actions 只需要上述
受限的 sudo 命令。

## Nginx 配置

仓库的 `etc/keywordiq-v2-nginx.conf` 与 V2 默认部署值配套：它为
`key.liangtong.online` 提供 HTTPS、将 `/api/` 与 `/health` 反代至 8001，并从
`/var/www/keywordiq-web` 托管 SPA。首次安装或核对时：

```bash
cd /var/www/keywordiq
sudo install -o root -g root -m 0644 etc/keywordiq-v2-nginx.conf \
  /etc/nginx/conf.d/key.liangtong.online.conf
sudo nginx -t && sudo systemctl reload nginx
```

证书文件须已位于 `/etc/nginx/ssl/key.liangtong.online/fullchain.crt` 与
`/etc/nginx/ssl/key.liangtong.online/privkey.key`，与已上线的 V2 站点一致。

## GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions** 新建下列仓库 secrets：

| Secret | 内容 |
| --- | --- |
| `ECS_SSH_HOST` | `120.55.95.30` |
| `ECS_SSH_PORT` | SSH 端口，通常为 `22` |
| `ECS_SSH_USER` | `www` |
| `ECS_SSH_PRIVATE_KEY` | 专用于 Actions 的 Ed25519 私钥全文 |
| `ECS_SSH_KNOWN_HOSTS` | ECS 的 SSH host key 行 |

在你的电脑生成专用密钥：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/keywordiq_v2_cd -C github-actions-keywordiq-v2
```

把 `~/.ssh/keywordiq_v2_cd.pub` 的一整行追加到 ECS 的 `~/.ssh/authorized_keys`，权限设为
`700 ~/.ssh` 和 `600 ~/.ssh/authorized_keys`。私钥文件内容填入 `ECS_SSH_PRIVATE_KEY`。

从可信网络获取并核对主机指纹后，再保存 known-hosts 内容。例如在本机执行：

```bash
ssh-keyscan -H 120.55.95.30
```

将输出中与你已核对指纹相符的 `ssh-ed25519` 行填入 `ECS_SSH_KNOWN_HOSTS`。

## 发布与失败处理

只在 `v2` 分支的 Actions 页面运行 **Deploy V2 to ECS**。工作流拒绝 CI 未成功、非 `v2`
分支、非 `origin/v2` 历史中的 SHA，或 ECS 中有已跟踪本地修改的情况。失败时 Actions 日志会
停在准确阶段；数据库迁移已经执行时，不会自动反向迁移。
