# 在管理页面登记游戏后端

使用有 Game client 添加/修改权限的 SSO 管理员登录 `/admin/`。

1. 进入 Accounts → Game clients → 增加。
2. ESE 填写 Id `ese`、Name `ESE`，保持 Enabled 勾选，Web client id 留空。
3. 保存后复制页面显示的 `SSO_SERVICE_ID` / `SSO_SERVICE_KEY`，写入 ESE 服务器的私有环境文件。
4. ESE 的 `SSO_ISSUER` 设置为 `https://sso.ourtaiko.org`，数据库配置仍指向 ESE 自己的 PostgreSQL。
5. 用 ESE 所在目录的 Compose 配置重新创建后端容器，让新环境变量生效。

密钥由服务器随机生成，仅在保存成功的响应中显示。数据库只保存 SHA-256 哈希，
原文不进入会话、消息或管理员审计日志。不要刷新/重新提交密钥结果页；离开后不能查看原文。
丢失密钥时进入该服务的设置，点击“重置服务密钥”，确认后保存新密钥并更新业务后端。
重置立即使旧服务密钥失效，但不删除已有游戏会话。普通编辑不更换密钥。
重置操作受管理员修改权限与 CSRF 保护，并记录不含密钥的审计日志。

ESE 没有网站 OAuth 登录需求，无需添加 OAuth Toolkit Application。
Fanmade 的既有部署通过 `provision_fanmade` 命令登记游戏服务和网站 OAuth Application；
该命令仍保留，管理界面更新不会重新生成既有服务的密钥。
