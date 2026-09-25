# Docker 双栈与本地迁移演练

SSO 仓库负责账号中心和共享 PostgreSQL；Fanmade 仓库负责业务后端、业务表迁移和文件。
部署分为两套，容器内部仍各自独立：

| Compose 项目 | 服务 | 持久化内容 |
| --- | --- | --- |
| SSO：`deploy/compose.stack.yml` | `sso`、`postgres` | 账号中心数据卷、PostgreSQL 集群数据卷 |
| Fanmade：`deploy/compose.yml` | `fanmade` | 音频和 TJA 文件卷 |

PostgreSQL 内有 `ourtaiko_sso` 和 `ourtaiko_fanmade` 两个库，分别属于独立非超级用户角色。
角色不能连接对方的数据库。Fanmade 通过 SSO HTTP/OIDC 接口取得身份，不直接访问账号库。
封面二进制保存在 Fanmade 数据库的 `chart_covers.webp`，不在文件卷中。

镜像仅包含程序和运行依赖；数据库、文件、凭据、签名密钥都不进入镜像。
Fanmade 镜像有 FFmpeg、ffprobe、cwebp，但没有 PostgreSQL 服务端。

## 当前适用范围

这套脚本是 **本地开发环境迁移演练**，不会 SSH、推送代码、部署生产或恢复到已有数据库。
2026-09-24 已另行完成生产迁移，当前配置为 `deploy/compose.production.yml`，详见 [生产记录](PRODUCTION.md)。
旧的 1Panel `deploy/compose.yml` 保留为历史 host 网络配置，不用于当前生产。
原 Fanmade PostgreSQL 配置移到本仓库 `compose.postgres.legacy.yml`，保留原项目名和实际磁盘路径，
仅保留迁移前布局作参考；不要对当前生产执行该 legacy 配置。

`docker_lab.py` 只接受原生 `ourtaiko_sso_dev` / `ourtaiko_fanmade_sso_dev`，要求 DEBUG、loopback，
并检查原生 SSO 和 API 已停服，避免备份时上传。还需要停止其他开发写入程序。
目标限制为 `ourtaiko-lab-*`，拒绝覆盖已有目标目录、Compose 容器和卷。
首次使用前在原生环境完成当前代码的 schema 迁移，并保留升级前备份。

## 本地运行

要求 Docker Desktop 已启动、Docker CLI 可用、Compose 2.30+（支持 raw env_file）、PostgreSQL 18 客户端。
两个仓库维持工作区布局：`OurTaikoSSO` 与 `Fanmade/backend`。
下面命令从 **OurTaikoSSO 根目录**执行。Mac Docker CLI 未加入 PATH 时先执行：

```sh
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
```

构建程序镜像：

```sh
docker build -t ourtaiko-sso:local .
docker build -t ourtaiko-fanmade:local ../Fanmade/backend
```

准备原生开发数据包，然后先恢复 SSO/共享数据库，再恢复 Fanmade：

```sh
.venv/bin/python deploy/docker_lab.py prepare --bundle raw-backup
.venv/bin/python deploy/docker_lab.py restore --name first --bundle raw-backup
```

脚本自动创建独立数据库角色、恢复两个库、恢复签名密钥及文件，调整 **副本** 的本地回调 URI，
检查原服务凭据是否匹配，然后按顺序启动服务。源环境的密钥、账号 ID、文件和应用配置不被改写。
恢复启动前不运行 `seed_dev` 或旧身份导入流程。

| 入口 | 地址 |
| --- | --- |
| SSO 页面 | `http://127.0.0.1:18090` |
| Fanmade API 就绪检查 | `http://127.0.0.1:18080/readyz` |
| Fanmade API | `http://127.0.0.1:18080/api/v1` |
| Docker PostgreSQL | `127.0.0.1:15433` |

本演练不包含 Fanmade 前端，所以 API 的 `/me/profile` 等网页路径会返回 404；
OIDC 回调后的身份通过 `/api/v1/me` 验证。正式部署还需要前端和 HTTPS 反向代理。

容器间使用共享桥接网络，数据库主机为 `postgres`。
浏览器看到的 SSO issuer 仍为 `http://127.0.0.1:18090`，Fanmade 的 `SSO_CONNECT_ADDRESS=sso:8090`
只改变访问该 issuer 的 TCP 目标，不改 HTTP Host、OIDC issuer 校验、浏览器 URL 或 HTTPS 证书验证。
正式 HTTPS 部署通常留空此配置；如果指定目标，该目标必须提供匹配公开 issuer 的 TLS。

## 验证真实流程及二次恢复

```sh
# 首次恢复会自动比较原账号、业务数据、签名密钥和所有文件哈希。
.venv/bin/python deploy/docker_smoke.py seed --name first

# 短暂停止两套应用写入，分别打包数据库/文件/配置，再恢复应用。
.venv/bin/python deploy/docker_lab.py backup --name first --bundle docker-backup

# 保留 first 的旧卷；全新的 restored 卷模拟新服务器。
.venv/bin/python deploy/docker_lab.py stop --name first
.venv/bin/python deploy/docker_lab.py restore --name restored --bundle docker-backup
.venv/bin/python deploy/docker_smoke.py check --name restored
```

`seed` 只在测试副本创建标记账号、谱面、WebP 封面和成绩；测试账号密码放在忽略的
`.data/docker-lab/migration-fixture.json`，不输出。样本会保留，供 `check` 在二次恢复后验证。
`check` 会重新登录，因此活动时间/会话可能变化；严格的数据库指纹校验应在恢复后、交互前执行。
不要把演练样本或本地凭据用于生产。

每个备份目录都有 SHA-256 清单，恢复前验证完整性。结构为：

```text
.data/docker-lab/bundles/<备份名>/
  sso/       database.dump + data.tar.gz + env
  fanmade/   database.dump + files.tar.gz + env
  roles.json + postgres.env + baseline.json + manifest.json
```

这些是 **含机密的本地开发备份**，外层目录权限为 0700，文件通常为 0600，并被 Git 和镜像构建忽略。
它们没有加密，不能上传公开仓库或镜像仓库；正式异机传输前需要加密和可信传输渠道。
两个组件的备份内容分开保存，本地脚本统一协调顺序。它不替代生产备份保留策略或灾难恢复工具。

## 启停与检查

```sh
.venv/bin/python deploy/docker_lab.py stop --name restored
.venv/bin/python deploy/docker_lab.py start --name restored
docker compose --env-file .data/docker-lab/restored/compose.env -f deploy/compose.stack.yml ps
docker compose --env-file .data/docker-lab/restored/compose.env -f ../Fanmade/backend/deploy/compose.yml ps
```

`stop` 保留容器和卷，`start` 不重新导入数据库。由于本地端口相同，同一时间只能启动一套演练环境。
发生部分恢复失败时脚本不删除已恢复内容；检查对应容器后，使用新的演练名称重新执行，或明确清理
该失败演练的容器/卷。不得为了重试删除原生数据库、生产目录或备份包。

## 真正跨服务器迁移还需处理

- 在最终备份前停止所有业务写入；保持账号库、业务库和文件之间的一致性。
- 保留账号 ID、应用服务凭据、数据库角色、Django Secret Key、签名密钥、Fanmade 会话加密密钥。
- 传输镜像及备份；`docker save` / `docker load` 只搬镜像，持久数据仍靠备份包恢复。
- Apple Silicon 默认构建 `linux/arm64`；现有生产服务器为 `linux/amd64`。迁移时必须构建目标架构镜像，
  或通过 `docker buildx build --platform linux/amd64,linux/arm64` 发布多架构镜像。
- 生产配置关闭 DEBUG、启用 HTTPS/Secure Cookie、配置域名、反向代理、SMTP 和正确的 OAuth 回调。
- 在新服务器完成验证后再切换 DNS，并保留旧服务器用于回退。
