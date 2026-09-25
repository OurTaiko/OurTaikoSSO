# Production deployment

## Docker 双栈迁移（2026-09-24）

已从“SSO 容器 + 独立 PostgreSQL 容器 + Fanmade systemd”切换为两套 Compose：

| Compose 项目 / 配置 | 服务 | 持久卷 |
| --- | --- | --- |
| `ourtaiko-sso` / `/opt/ourtaiko-sso/docker-compose.yml` | `ourtaiko-sso`、`ourtaiko-postgres` | `ourtaiko-sso-data`、`ourtaiko-postgres-data` |
| `ourtaiko-fanmade` / `/opt/ourtaiko-fanmade/docker-compose.yml` | `ourtaiko-fanmade` | `ourtaiko-fanmade-files` |

配置源分别为本仓库 `deploy/compose.production.yml` 和 Fanmade 的同名文件。
共享桥接网络为 `ourtaiko-shared`，三个卷均为显式 external 持久卷。
PostgreSQL 仍承载两个独立业务数据库，账号/权限/密码哈希按 globals 原样恢复；没有重新创建应用身份。
SSO/Fanmade 通过各自数据库账号连接 `postgres:5432`，Fanmade 通过原公网 HTTPS issuer 访问 SSO。
SSO、API、数据库的宿主机入口仍分别为 loopback 8090、8080、5432，域名、证书、反向代理和前端未变。

服务器构建并验证的镜像为 **linux/amd64**：

- SSO：`ourtaiko-sso:docker-2fd9ac4`，同时标记 `ourtaiko-sso:release`。
- Fanmade：`ourtaiko-fanmade:docker-815e3bb`，同时标记 `ourtaiko-fanmade:release`。
- PostgreSQL：原有 18.6 Alpine 的固定 digest，未升级主版本。

新私有配置：`/etc/ourtaiko-sso/docker/sso.env`、`/etc/ourtaiko-sso/docker/postgres-password`、
`/etc/ourtaiko-fanmade/docker/api.env`，仅 root 可读。应用密钥、签名密钥、正式回调及用户 ID 保留。
Fanmade 的可信代理改为共享桥接网络网关的精确 `/32`；若重建网络导致网关改变，必须同步调整。
SSO 在容器内监听 `0.0.0.0:8090`，API 监听 `0.0.0.0:8080`，宿主机映射仅绑定回环。

### 全量备份与实际恢复验证

以下目录仅在服务器上，权限受限，含密码哈希/密钥，不可提交 Git 或公开：

- 在线初备：`/var/backups/ourtaiko-docker-migration/20260924T234434Z-initial`。
- 停写最终备份：`/var/backups/ourtaiko-docker-migration/20260924T235243Z-final`。
- 执行记录和验证计数：上述父目录的 `state.json`、`verification.json`；一次性迁移/验证脚本也保存在该受限目录。

备份包含所有非模板数据库（`ourtaiko_sso`、`ourtaiko_fanmade`、`postgres`）的 custom dump、
完整集群 SQL、globals/角色、原业务文件、签名密钥、私有配置、旧程序和 systemd/Compose 配置。
备份带 SHA-256 清单，已复核。先恢复到独立预检卷并启动两个 AMD64 应用；
正式停写后重新备份，再恢复至新的生产卷，所有用户表完整行指纹一致，签名密钥和 52 个资源文件哈希一致。
**本次已实际恢复并启动验证，非仅检查 dump 目录。**

公网验证通过：网页 OIDC/PKCE、加密会话、游戏登录/bootstrap、退出、原有音频/TJA/WebP 下载和哈希。
临时测试账号与其会话使用 finally 清理，没有修改真实用户凭据。验证前后均为 SSO 9 账号，
Fanmade 9 用户 ID、26 谱面、51 成绩、15 封面、52 文件；这些是当时快照，不是后续应强制恢复的数量。
首轮 HTTP 测试脚本遗漏 HTTPS CSRF 所需 Referer/Origin，补齐浏览器头后通过，未因此修改应用安全设置。
本次未做 YataiDON 真机游玩、真实邮件发送或前端重新发布。

### 留存与回退边界

旧 PostgreSQL 容器保留为 `ourtaiko-postgres-before-docker`，已停止且 restart=no；
原 `/var/lib/ourtaiko-fanmade/postgres`、`/var/lib/ourtaiko-fanmade/files`、`/var/lib/ourtaiko-sso`
保留为迁移前副本，**不再是活动数据位置**。旧镜像保留 `before-docker-migration` 标签。
旧 `ourtaiko-fanmade` systemd 服务 inactive/disabled，单元和二进制保留。
隔离预检的三个 stage 容器和 stage 卷已清理；当前生产卷与旧生产数据未删除。

迁移发布后已经恢复写入，旧库是切换前快照。**不能直接启动旧库当作无损回退**；
需要先停写并备份当前新库/文件，再规划应用回退或数据恢复。旧 `compose.postgres.legacy.yml`
仅供迁移前布局参考，不应对当前生产运行，否则会与现有容器冲突。

### 当前运维

```sh
sudo docker compose -f /opt/ourtaiko-sso/docker-compose.yml ps
sudo docker compose -f /opt/ourtaiko-fanmade/docker-compose.yml ps
sudo docker logs --tail 100 ourtaiko-fanmade
sudo docker logs --tail 100 ourtaiko-sso
sudo docker logs --tail 100 ourtaiko-postgres
```

Fanmade `deploy/update.sh` 检测 `/opt/ourtaiko-fanmade/docker-compose.yml` 后构建并更新容器，
不再启动旧 systemd 服务。SSO 更新同样使用当前 Compose 的 build/up；更新前保留镜像和一致数据备份。
修改 env_file 后使用 `docker compose up -d --force-recreate <服务>`，单纯 restart 不会重新加载环境配置。
日后完整备份应从 **当前 PostgreSQL 和当前持久卷** 导出，并包含这两套新 Compose/私有配置，不能只复制旧目录。
如果再次迁移机器，先准备网络与卷、恢复角色/数据库及数据文件，再启动 SSO 栈，最后启动 Fanmade 栈。


## Internationalization release (2026-09-22)

- SSO `a4a3cb0`, Fanmade API `2011c90`, and frontend `665fbf7` are live.
- SSO migration `accounts.0003_user_preferred_language` completed. All 8 existing
  accounts received the default `zh-hans`. No account reimport or business schema
  migration was needed. Signing keys, credentials and existing user data were retained.
- The SSO image is `ourtaiko-sso:i18n-a4a3cb0`, also tagged `release`; the existing
  1Panel Compose deployment remains in use. The API uses its existing systemd service.
- [Frontend CI deployment](https://github.com/OurTaiko/Fanmade_Frontend/actions/runs/35776258424)
  passed lint, tests, build and publishing. The site release points to
  `releases/665fbf722b4ab633bfb6be61ed9b36c0db99fb9d`.
- Production verification passed real OIDC login, default Chinese, saved preference
  changes in English/Japanese/Korean/Chinese, live preference propagation to an
  existing browser session and game token, localized song titles, mobile layout,
  logout, and absence of browser script errors. Temporary verification identities
  and their SSO tokens/browser sessions/business identities were removed.
- Song title/subtitle selection is **preferred language → original field**.
  Chinese is the default account preference, not an intermediate song-text fallback.

Root-only release backup: `/var/backups/ourtaiko-i18n/20260922`. It contains a
pre-migration SSO database dump (verified readable with `pg_restore --list`, not a
restore drill), previous API binary, Compose file, revisions and frontend target.
The previous SSO image is retained as `ourtaiko-sso:before-i18n-20260922`.
The new column is additive: application rollback can retain it. Do not rerun the
original identity import or restore an old business database for this release.

The server could not authenticate a direct private GitHub fetch, so the exact
already-pushed SSO/API commits were transferred in verified Git bundles over SSH
and fast-forwarded. Frontend publishing used its existing CI credentials.

## Previous production topology (before 2026-09-24 Docker migration)

The SSO container is managed by **1Panel → Containers → Compose → ourtaiko-sso**.
It runs the production Gunicorn image `ourtaiko-sso:release`, with a read-only
filesystem and a non-root user, listening on `127.0.0.1:8090`.

1Panel owns the separate reverse-proxy website `sso.ourtaiko.org`:
`https://sso.ourtaiko.org`, with a dedicated Let's Encrypt certificate requested
through **1Panel → Websites → Certificates**, using the existing ACME account
and HTTP validation. Automatic renewal is enabled. The certificate initially
expires 2026-12-19. HTTP redirects to standard HTTPS port 443.

The proxy overwrites Host, forwarded scheme and client address. Fanmade's
`SSO_ISSUER` and SSO's base URL/allowed host use `sso.ourtaiko.org`; the Fanmade
callback stays `https://fanmade.ourtaiko.org/api/v1/auth/sso/callback`.

The former `fanmade.ourtaiko.org:8443` website has been removed through 1Panel,
and its server firewall rule removed. Only the internal SSO process uses 8090.
The separate `ourtaiko-sso` container/compose remains managed by 1Panel.

## Previous server files (historical paths)

- Source checkout: `/opt/ourtaiko-sso/src`
- 1Panel compose source: `/opt/ourtaiko-sso/docker-compose.yml`
- Environment: `/etc/ourtaiko-sso/sso.env` (root-only)
- Persistent signing key: `/var/lib/ourtaiko-sso/oidc-private.pem`
- Separate database and non-superuser role: `ourtaiko_sso`
- Prepared Fanmade environment: `/etc/ourtaiko-sso/fanmade-next.env`
- Read-only import connection: `/etc/ourtaiko-sso/import.env`
- Prepared new backend binary: `/tmp/ourtaiko-api-sso` (rebuild if absent)

SMTP uses the existing production mail service. TLS authentication was checked
without sending mail.

## Verified release and domain migration (2026-09-20)

- Backend `5492b09` and frontend `50bde22` are pushed to `main` and live.
- SSO is built from this repository, with versioned static assets collected into
  the production image. The site logo and favicon use the shared `OurTaiko/icon.png`.
- [Frontend deployment workflow](https://github.com/OurTaiko/Fanmade_Frontend/actions/runs/35542581510)
  passed dependency install, lint, tests, build and SSH publishing.
- All 7 legacy IDs, usernames, nicknames, bcrypt hashes, email/verification flags
  and Fanmade admin roles were compared before migration. No profile fields or
  passwords remain in Fanmade's `users` table: it contains only `id`.
- Business schema is 18. Final counts remain 7 users, 26 charts and 48 scores;
  the separate SSO database contains the same 7 users.
- Production Chromium verification passed real frontend login → SSO consent →
  OIDC/PKCE callback, legacy bcrypt login, account-center navigation, chart upload,
  game login without cookies, score storage and idempotent retries, nickname
  propagation to web/game/chart/leaderboard, password-change revocation of both
  session types, old-password rejection, re-login and logout.
- The test used a disposable account, chart and score, with automatic cleanup in
  `finally`. All temporary records were removed; the durable file-deletion queue
  was verified empty afterwards. No verification mail was sent.
- The full browser/game verification was repeated successfully on the dedicated
  SSO domain, including consent/callback across subdomains and account-center links.
- Original account passwords remain valid. The SSO site may ask users to log in
  again after moving to its own hostname.

Final pre-migration backup (API stopped before dump):
`/var/backups/ourtaiko-sso-migration/20260920T224353Z`.
This includes the original database/files, old backend binary/environment,
frontend release target, and the successful account-import comparison report.

Validated post-migration backup of both databases, application files, private
configuration and signing key:
`/var/backups/ourtaiko-sso-migration/post-20260920T225108Z`.
Keep both backup directories root-only; they contain secrets.

Configuration and old-site backup before the hostname migration:
`/var/backups/ourtaiko-sso-migration/domain-before-20260920`.
The account database, signing key and service/client credentials were preserved.

## Cutover and rollback runbook

1. Verify the public SSO URL, trusted certificate, `/readyz`, login page and OIDC
   discovery from an external client. If changing the hostname, update SSO base
   URL/allowed hosts, Fanmade issuer, proxy Host, and certificate together.
2. Stop `ourtaiko-fanmade` to freeze legacy account writes, and create a **fresh**
   database dump plus application/configuration backup. The original cutover used the final pre-migration backup documented above.
   For any later migration, take another fresh backup.
3. Run `import_fanmade` dry-run and `--apply` with both SSO and import environment
   files. Verify every source ID, username, nickname, bcrypt hash, email,
   verification flag and admin role before dropping legacy account columns.
4. Install the prepared Fanmade environment with the same root:ourtaiko owner
   and mode 0640 as the old environment, install the new backend binary, then
   start `ourtaiko-fanmade`. Migration 018 checks all existing IDs against SSO
   before committing. Verify `/readyz`, charts and score ownership/counts.
5. Advance frontend `main` to the verified SSO commit and push. Watch the existing
   GitHub Actions deploy job and confirm the new 1Panel release symlink.
6. Verify browser OIDC callback/logout and account-center links, plus legacy
   game login/token validation/score operations. Use a disposable test account
   and remove only its data afterwards. Old sessions require login again.
7. Back up both final databases and retain the private configuration/signing key.

If migration fails, keep the frontend on its old release. Restore the old backend
binary and environment; if migration 018 committed, restore the fresh pre-cutover
Fanmade dump while the API is stopped, before restarting the old binary. Keep
SSO data and all original backups until rollback has been verified.
