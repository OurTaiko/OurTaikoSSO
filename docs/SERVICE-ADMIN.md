# 在管理页面登记业务应用

以下为统一 Application 的本地新版，尚未发布到生产。生产更新必须等待用户批准。
完整升级、验证和回退过程见 [APPLICATION-MIGRATION.md](APPLICATION-MIGRATION.md)。

1. 使用有 Application 添加/修改权限的 SSO 管理员登录 `/admin/`，进入 Applications。
2. 创建应用，填写名称、唯一 Client ID，后端应用选 confidential。
3. 网站选“网站登录”，填写精确回调；仅后端的服务选“仅后端接口”，回调留空。
4. 按业务需要启用游戏接口、用户资料查询、网站令牌查询权限。
5. 保存后将只显示一次的密钥存入业务后端私有配置。网站登录使用
   `SSO_CLIENT_ID` / `SSO_CLIENT_SECRET`，内部接口使用
   `SSO_SERVICE_ID` / `SSO_SERVICE_KEY`，两者填写同一份 Application ID/Secret。
6. 应用级管理员在 Client roles 中选择用户和 Application，勾选 Is admin。
   业务后端必须读取并执行这个角色，单纯添加角色不会改变尚未接入的业务代码。

普通编辑不会改变密钥。重置密钥会使旧 Application 密钥失效，并关闭迁移前的服务凭据；
必须更新所有调用方并重建容器或重新部署。原密钥不能找回。

现有 Fanmade 通过迁移合并到原 OAuth Application，无需重新登记。旧服务凭据只用于过渡，
更新后端并验证后关闭兼容开关。ESE 尚未部署，不安排生产迁移；首次部署直接创建一个
Client ID 为 `ese` 的后端 Application，新生成的 64 位 hex 密钥兼容其现有配置格式。
ESE 只需配置 SERVICE 两个变量，数据库仍指向它自己的 PostgreSQL。

`provision_fanmade` 保留为旧部署脚本的兼容入口，不再创建 GameClient；它仍接受旧式双凭据
以供过渡。新接入使用上述 Application 页面，不需要额外生成服务密钥。
