# OurTaikoSSO

独立的本地账号中心：Django 5.2 LTS 一体化页面和后端，Django OAuth Toolkit 提供 OIDC 授权码 + PKCE。游戏通过受保护的服务端兼容接口认证，YataiDON 的登录协议不需要改变。

## 本地运行

默认地址 **http://127.0.0.1:8090**。开发数据库为本机 PostgreSQL 的 `ourtaiko_sso_dev`，与原来的 `ourtaiko_fanmade` 分开。依赖由 `uv.lock` 固定，需要 Python 3.12+、uv 和 PostgreSQL。

```sh
uv sync --frozen
.venv/bin/python scripts/setup_dev.py
# PostgreSQL 已启动的情况下，仅首次创建：
createdb ourtaiko_sso_dev
.venv/bin/python manage.py migrate
.venv/bin/python manage.py seed_dev
sh scripts/dev.sh
```

本机 Python 也可使用 Codex 随附的运行时，首次安装时传给 `uv sync --python <python路径>`。开发环境的 `.env`、签名私钥、客户端密钥和数据库文件都不提交。`setup_dev.py` 不覆盖已有文件；`seed_dev` 不重置现有账号密码或客户端密钥。

测试账号的登录名为 `ssoDev`，随机生成的密码在 **`.data/dev-access.txt`**。服务凭证在 `.data/dev-clients.json`。文件权限为 0600。测试账号没有管理员权限；需要后台管理时使用 `manage.py createsuperuser` 单独创建管理员。

本机已有 PostgreSQL 集群可通过以下命令启动，不要重新 initdb：

```sh
/opt/homebrew/opt/postgresql@18/bin/pg_ctl -D /opt/homebrew/var/postgresql@18 -l /tmp/ourtaiko-postgresql.log start
```

没有 PostgreSQL 时，可将 `DATABASE_URL` 设置成 `sqlite:///<绝对路径>/.data/dev.sqlite3` 进行本地开发；自动测试始终使用隔离的内存数据库。

## 已实现

- 注册、验证邮箱和重发验证链接；首次激活要求 POST 确认。
- 登录、退出、昵称修改；查看稳定用户 ID、登录名、邮箱验证状态。
- 修改密码、通过已验证邮箱重置密码。修改密码撤销游戏/OAuth 凭证和其他浏览器会话。
- 浏览器与游戏登录列表、单个会话撤销、退出所有设备。
- 网站 OIDC 授权登录、RS256 ID token、发现文档、JWKS、UserInfo、授权撤销。
- 本地演示应用：账号概览底部“开始体验”，完成授权码 + PKCE、签名/issuer/audience/nonce 校验及 UserInfo 查询。
- 游戏认证内部 API：按服务签发 64 位十六进制 token、验证、撤销、批量公开昵称查询。
- 服务之间的 token 隔离、应用级管理权限、浏览器 CSRF、账号/IP 登录限流。
- 保留原用户 ID 和 bcrypt 密码哈希的本地 Fanmade 导入工具。

页面不依赖外部 CDN、字体、JavaScript 框架或前端构建服务。

## 开发邮件

默认不发送真实邮件，注册验证和密码重置邮件写入 **`.data/emails/`**。打开最新文件，复制其中的链接到浏览器即可完成流程。重置密码只发送到已验证邮箱；导入的历史未验证邮箱不会获得密码重置权限。

真正发送邮件时在 `.env` 配置 `EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend` 及 SMTP 参数。不要直接复制或提交原服务的 SMTP 密码。

## 导入已有测试账号

`.env` 中 `FANMADE_SOURCE_DATABASE_URL` 指向原本机测试库。源连接强制只读，导入使用事务，默认只做演练：

```sh
.venv/bin/python manage.py import_fanmade
.venv/bin/python manage.py import_fanmade --apply
```

保留原用户 ID、登录名、昵称、密码哈希、邮箱及验证状态、注册时间。原来的 Fanmade 管理员映射为该应用的角色，不授予 SSO 管理员或 ESE 管理员权限。bcrypt 哈希直接兼容验证，成功登录后升级为 Django 默认密码哈希；不需要原密码。

重复导入跳过已存在的 ID，不覆盖 SSO 中修改过的资料或密码。名称/邮箱/ID 冲突会回滚整次导入。旧会话不迁移，需要重新登录。这里是一次性迁移副本，不做双向同步；上线切换时必须冻结旧系统的账号写入，再进行最后导入与切换。

## 游戏后端接入

详见 [API.md](docs/API.md)。Fanmade/ESE 的公开 `/api/v1/game/login` 保持不变，由它们的后端调用：

```text
POST /internal/v1/game/login
X-Service-ID: fanmade
X-Service-Key: <该服务的私有密钥>
Content-Type: application/json

{"username":"玩家登录名","password":"玩家密码"}
```

成功响应保持现有 `user / accessToken / expiresIn` 结构。业务后端在需要身份时调用 introspect，使用返回的 `user.id` 保存业务记录，不接受客户端声明的用户 ID。服务密钥只保存在业务后端，不能打包进 YataiDON 或网页。

此项目只完成独立账号中心。**现有 Fanmade 网站、Fanmade 后端和 YataiDON 尚未切换到这个 SSO；ESE 曲库服务也不属于本项目。** 本地演示应用用于验证标准网站登录流程，不代表旧网站已接入。

## 网站接入

发现文档：`http://127.0.0.1:8090/.well-known/openid-configuration`。每个网站注册单独的 confidential client 和明确的回调地址；使用 Authorization Code + S256 PKCE，请求 `openid profile`。业务端校验 state、nonce、签名、issuer、audience、有效期并建立自己的会话。

`sub` 是稳定的 32 位用户 ID。`profile` scope 提供 nickname 和 preferred_username，不提供邮箱或全局管理员权限。第三方应用公开自助注册未启用；由管理员配置客户端。游戏兼容认证与 OIDC 授权流程是两套端点，使用不同凭证。

撤销授权会撤销 SSO 保存的凭证，但不自动删除外部网站已建立的本地会话；接入方需要落实会话过期和撤销策略。本开发版未实现跨站 back-channel logout。

## 验证与维护

```sh
.venv/bin/python manage.py test accounts --settings=config.test_settings
.venv/bin/ruff check .
.venv/bin/python manage.py check
.venv/bin/python manage.py smoke_live # 服务运行时，使用临时账号验证真实 HTTP/PostgreSQL 链路
.venv/bin/python manage.py cleanup_auth
```

测试覆盖原游戏响应格式、服务密钥、token 受众/过期/撤销、权限隔离、资料同步、CSRF、限流、注册验证、密码修改/找回、旧 bcrypt 兼容、OIDC 签名/PKCE/一次性授权码及非法回调。

`cleanup_auth` 清理已过期会话、授权 token 和限流记录，部署后适合每天运行。`/healthz` 检查进程，`/readyz` 检查数据库连接。

## 开发版边界

本轮为本地开发：没有部署公网、没有发送真实邮件，没有更改原业务数据库。生产部署需单独配置 HTTPS、服务凭证及允许的回调地址，设置 `DJANGO_DEBUG=false`，使用生产 WSGI/ASGI 服务和静态资源托管。代理来源头默认不受信任；业务后端和入口代理还应对原始客户端的登录请求限流。

不能通过给多个网站共享 `.ourtaiko.org` Cookie 来代替 OIDC。业务后端无需连接用户数据库，昵称通过受保护的批量接口获取，可在业务服务内短暂缓存。

参考：[Django 认证](https://docs.djangoproject.com/en/5.2/topics/auth/default/)、[OAuth Toolkit OIDC](https://django-oauth-toolkit.readthedocs.io/en/stable/oidc.html)。

## 已接入 Fanmade

相邻 `../Fanmade/backend` 与 `../Fanmade/frontend` 已使用本项目认证。网站地址为 http://127.0.0.1:5173；完整启动/迁移说明见 [Fanmade SSO 接入](../Fanmade/backend/docs/SSO.md)。`seed_dev` 也登记 `ourtaiko-fanmade-web` 客户端和精确回调 URI。网站登录采用 OIDC + PKCE，游戏仍沿用原 API，由后端转接 SSO。

运行 `.venv/bin/python manage.py smoke_fanmade` 可验证真实的登录、上传、游戏成绩、昵称同步和密码撤销；仅允许本地独立开发库，完成后清理本次记录。
