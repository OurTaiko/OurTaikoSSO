# 统一 Application 与旧服务凭据清理

Application 是唯一业务身份。Client roles 和 GameSession 直接关联 OAuth Application；
ApplicationAccess 只存启用状态及游戏、资料、网站令牌接口权限，不存第二套凭据。
管理方式见 [SERVICE-ADMIN.md](SERVICE-ADMIN.md)。

网站与内部 API 共用 Client ID/Secret：Fanmade 的 `SSO_SERVICE_ID = SSO_CLIENT_ID`、
`SSO_SERVICE_KEY = SSO_CLIENT_SECRET`。原变量名保留，值必须相同。纯后端只需 SERVICE 两个变量。
密钥仅保存在业务后端私有环境中。普通编辑不轮换密钥；重置时必须同步更新全部调用方。
停用 Application 同时拒绝 OAuth 和内部接口；重新启用可恢复尚未过期的令牌。

## 已执行的第一阶段

生产已部署 SSO `91854aa`、Fanmade `3fcc565`，完成 0004 数据迁移及 Fanmade 凭据切换。
原 OAuth 应用字段、用户、角色、会话和 Fanmade 数据均已核对；旧服务凭据已关闭并验证拒绝。
公网 HTTPS 的 OIDC/PKCE、游戏登录、实时角色变更及原会话继续验证均通过。
生产 SSO 停写备份已恢复至独立临时库并核对全部行指纹，之后删除临时恢复库。
备份位于服务器受限目录 `/var/backups/ourtaiko-application-unification/`，
`latest-stage1` 指向此次备份，不含任何本地合成身份。

ESE 尚未部署，不安排生产迁移；首次上线直接创建纯后端 Application。
HachiCats 的 OAuth 应用保持原样，本次没有迁移其 ADMIN_USERNAMES 管理员逻辑。

## 当前版本的清理迁移 0005

本地删除兼容代码、测试并提交后，再在服务器部署该提交、执行迁移：

1. 检查所有调用方已使用 Application 凭据。0005 遇到任意 `accept_legacy=True` 会拒绝执行。
   如从更老版本升级，必须先部署第一阶段版本完成切换，不能直接跨过该步骤。
2. 构建最终镜像，保留第一阶段镜像；短暂停止 SSO 和 Fanmade 写入，备份当前两库、配置与签名密钥。
3. 新镜像执行 `manage.py migrate --noinput`。0005 删除历史 GameClient 表和
   ApplicationAccess 的三个旧凭据字段；不改变 Application、角色、游戏会话或用户资料。
4. 核对数据与能力开关，先启动 SSO，再启动 Fanmade。验证健康、网站与游戏登录、角色、
   旧密钥拒绝以及管理页面不再包含兼容配置。测试身份用 finally 清理。

当前运行时代码、管理页面、provision_fanmade 均不再接受独立旧服务凭据。
0001–0004 历史 migration 保留，供已有数据库升级和新数据库重放，不能删除或伪造迁移状态。
新数据库没有旧客户端，可直接执行全部 migration。

## 本机 Docker 演练

独立 Compose 项目 `ourtaiko-unification-lab` 仅含合成数据；SSO/Fanmade 绑定
`127.0.0.1:28090` / `127.0.0.1:28080`。配置、测试签名密钥及备份均在忽略目录 `.data/unification-lab/`。

历史第一阶段 `deploy/unification_lab.py` 使用两个固定镜像：
`ourtaiko-sso:unification-before` 来自 `0c32502`，`ourtaiko-sso:unification-stage1` 来自 `91854aa`。
它验证旧版迁移、数据保持、恢复备份和关联冲突回滚；不要用最终版镜像替代其第一阶段镜像。
执行清理前先完成合成 Fanmade 调用方的凭据切换（同第一阶段 HTTP 联调）。

```sh
docker compose -f deploy/compose.unification-lab.yml build sso
.venv/bin/python deploy/unification_cleanup_lab.py
docker compose -f deploy/compose.unification-lab.yml run --rm --no-deps sso \
  python manage.py test accounts --settings=config.docker_test_settings
.venv/bin/python deploy/unification_http.py
```

清理脚本拒绝覆盖已有备份，先验证保护检查失败时数据库完全不变，再切换合成后端身份、
删除旧结构并比对其他全表数据及应用权限。HTTP 验证使用同一份非 hex 密钥进行网站/游戏认证。
未执行真实邮件发送或 YataiDON 真机游玩回归。

## 回退边界

0004 和 0005 均不可反向迁移。清理后不能只换回第一阶段镜像，更不能重启旧 SSO worker。
维护窗口尚未恢复写入时，可同时恢复该窗口数据库、对应镜像和私有配置；签名密钥保持原样。
恢复流量后优先向前修复；若必须回退，先停写备份当前状态并制定增量数据方案，
不能用旧备份覆盖新增用户、会话或业务写入。第一阶段之前的旧 Fanmade 配置已失效，不应再使用。
