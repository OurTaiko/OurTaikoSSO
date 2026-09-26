# 统一 Application（本地已实现，生产待批准）

本变更把 Application 作为唯一业务身份。管理页面不再展示 Game clients；
Client roles 和 GameSession 的 `client` 外键直接指向 OAuth Application。
`ApplicationAccess` 只是 Application 的一对一权限扩展，不提供独立应用身份或新的一份永久密钥。

## 新版管理方式

在 Applications 中配置名称、Client ID、应用密钥、网站回调和内部接口权限：

- 网站选择 Authorization code，填写精确回调，算法 RS256。
- 纯后端选择“仅后端接口”，回调留空。它不开放 OAuth client_credentials 发令牌；
  它通过已有内部 API 的服务头认证，网站 OAuth 仍只允许授权码/刷新。
- 内部接口必须是 confidential 应用，按需开启游戏、用户资料、网站令牌三个权限。
- 新建应用随机生成 64 位 hex 的 Client Secret，仅显示一次，数据库保存哈希。
- 后端 `SSO_SERVICE_ID = SSO_CLIENT_ID`、`SSO_SERVICE_KEY = SSO_CLIENT_SECRET`。
  原变量名保留以减少业务代码改动，两套变量指向同一份凭据。
- 角色在 Client roles 中直接选择 Application。应用所有者、SSO 工作人员或超级用户
  不自动获得业务管理员权限；业务后端仍须读取并执行 `isAdmin`。
- “启用应用”同时控制 OAuth 登录/令牌验证和内部接口。关闭后已有令牌的验证被拒绝，
  但记录不会删除；重新启用后未过期令牌可以恢复使用。
- 重置 Application 密钥会同时使旧应用密钥失效并关闭 legacy 凭据，必须同步更新所有调用方。
  普通编辑不会更换密钥。不要把密钥填入前端公开环境变量。

## 0004 数据迁移行为

1. 迁移前完整检查所有 GameClient：web_client_id 必须精确对应一个 confidential Application；
   多个服务绑定同一应用、找不到目标、服务 ID 与其他 Application 冲突均拒绝。
   不根据名称或用户名猜测关联，也不静默合并角色。
2. 已有 Fanmade 直接采用原 Application，原主键、Client ID、密钥哈希、回调以及 OAuth token 不变。
3. 原 ClientRole、GameSession 改挂同一个 Application；用户 ID、角色标记、游戏 token 哈希及到期时间不变。
4. 原服务 ID/哈希复制到该 Application 的 legacy 兼容字段，原 enabled 状态保留。
   旧 `X-Service-ID` / `X-Service-Key` 继续有效；新凭据同时可用。任何跨应用 token 都不能通过验证。
5. 无网站绑定的旧服务会创建纯后端 Application。新标准密钥随机生成后丢弃，只继续接受旧凭据；
   待管理员明确重置后才切换新凭据。用户已确认 **ESE 尚未部署，本次不安排 ESE 生产迁移**。
6. 只有 OAuth Application 而无旧 GameClient 的网站（例如 HachiCats）保持原样，内部接口默认不授权。
   HachiCats 目前依然执行自己的 ADMIN_USERNAMES，本次不改它的权限来源。
7. 旧 accounts_gameclient 表仅作历史审计保留，无管理入口，不参与运行时鉴权，后续版本另行清理。
   不要手工编辑它；它不会与新配置双向同步。

旧 SHA-256 服务密钥哈希无法还原成原文或转换成 OAuth 密钥，这是保留临时兼容字段的原因。
Application 的标准密钥哈希验证不接受 public 应用或明文存储模式。

## 本机 Docker 验证

固定独立项目 `ourtaiko-unification-lab`，全新 PostgreSQL 卷和网络，只有合成测试身份。
SSO 绑定 `127.0.0.1:28090`，Fanmade 绑定 `127.0.0.1:28080`。不使用生产环境文件。
私有配置/签名测试密钥、备份和结果保存在忽略目录 `.data/unification-lab/`。

```sh
# 先生成该目录下 postgres.env、sso.env、fanmade.env 和只读测试签名密钥。
# sso.env: UNIFICATION_LAB=true，数据库必须是此隔离 PostgreSQL 的 sso 库。
docker compose -f deploy/compose.unification-lab.yml up -d --wait postgres
docker compose -f deploy/compose.unification-lab.yml build sso
# 迁移前镜像从部署基线 0c32502 的 git archive 构建：
docker build -t ourtaiko-sso:unification-before .data/unification-lab/baseline
.venv/bin/python deploy/unification_lab.py
# 使用 PostgreSQL 实际运行测试，而非 SQLite：
docker compose -f deploy/compose.unification-lab.yml run --rm sso \
  python manage.py test accounts --settings=config.docker_test_settings
docker compose -f deploy/compose.unification-lab.yml up -d --wait sso
```

演练脚本拒绝覆盖已有数据库或备份；只在数据库仍与旧版指纹完全一致时允许重试。
脚本验证旧版 -> 新版的数据与认证兼容、旧数据库备份恢复及全表行指纹、
旧代码在恢复库上实际验证令牌，以及重复绑定导致的完整事务回滚。
合成数据不代表已核对生产所有配置；生产预检仍然必须执行。

## 生产执行计划（必须得到用户批准后）

此次没有连接或修改生产。以下步骤尚未执行。

1. 经批准后，用既有 `ourtaiko-prod` 连接核对当前代码、镜像、迁移版本、后台权限委派和实际应用映射。
   新版本只有 Application 管理权限可以修改应用；旧 `change_gameclient` 不自动升级成更大的 OAuth 管理权限。
   超级用户不受此差异影响；有委派管理员时须明确配置对应 Application 权限。
2. 构建并保留新 SSO/Fanmade 镜像，但暂不替换服务。用新 SSO 镜像和只读事务运行
   `manage.py check_application_migration`；只输出结果数量、不输出密钥。
   同时确认 Fanmade 的现有 OAuth secret 为有效可用的私有配置，绝不从哈希猜测或重新生成。
3. 安排短维护窗口，阻止 SSO 及 Fanmade 写入，停止旧 SSO/Fanmade 应用容器，保持 PostgreSQL 运行。
   导出当前 SSO 和 Fanmade 一致备份，保留私有环境、签名密钥、当前镜像/源码以及迁移前行指纹。
   用本地同样方式检查 dump，并确保可恢复。不得混跑新旧 SSO worker。
4. 通过 `compose run --rm --no-deps sso python manage.py migrate --noinput` 在新镜像执行 0004。
   核对原 OAuth 应用、用户、角色和游戏会话映射及 token 数量/内容，再启动新 SSO。
   先使用旧 Fanmade 服务凭据验证网站/游戏兼容，此时仍可使用旧版 Fanmade。
5. 升级 Fanmade 的凭据格式兼容代码，把其服务 ID/密钥改为现有 OAuth Client ID/Secret。
   OAuth 登录配置、issuer、回调、会话加密密钥不变；仅重建 Fanmade 容器，业务数据库无额外迁移。
   新版允许 40–1024 个可见 ASCII 字符的服务密钥，兼容现有 OAuth 非 hex secret。
6. 用临时身份验证完整 OIDC+PKCE 登录、游戏登录/查询/退出、旧会话、管理员角色与跨应用拒绝；
   测试结束清理临时身份。核对业务数据未变后，在 Fanmade Application 关闭“接受迁移前的服务凭据”。
   再验证旧服务凭据拒绝、新凭据正常，之后解除维护窗口。
7. HachiCats 原网站登录继续保留，本次不更新它的管理员逻辑。ESE 无线上部署，未来直接使用新 Application。
   不推送 Fanmade 前端或 HachiCats main 触发无关发布。

生产执行前会再次根据当时实际数据确认映射；预检失败则不进入维护/迁移阶段。

## 回退边界

0004 刻意标为不可反向迁移，因为恢复旧的角色/会话外键无法表示迁移后新建的任意应用。
不得直接切回旧 SSO 镜像，或伪造 migrations 状态。

- **维护窗口内且尚未开放写入**：停新应用，恢复该窗口的 SSO 数据库备份与旧镜像/配置；
  若 Fanmade 环境已切换则恢复旧环境及匹配镜像。签名密钥保持原样，验证原令牌后再恢复服务。
  本地已实际恢复并对比全表指纹，而非仅执行 pg_restore --list。
- **已恢复流量后**：优先修复前进。若一定要回退，先停写并备份新状态，制定增量数据迁移计划，
  不能直接覆盖回迁移前备份，否则会丢失新增用户、角色、登录和其他写入。
- **仅 Fanmade 切换失败**：只要 SSO 的 legacy 开关尚未关闭，可恢复 Fanmade 旧环境/镜像，
  继续使用新 SSO，不必回退数据库。关闭后先重新开启同一应用的 legacy 开关再回退业务端。

## 本次本地验证结果

- 45 项 SSO 测试在独立 Docker PostgreSQL 中通过；本地 SQLite 回归也通过。
- 原 OAuth Application 所有字段在迁移前后逐项一致；角色、游戏 token、OAuth token 和浏览器会话保留。
- 全量数据库备份实际恢复到独立数据库，全表行指纹一致，旧版代码可继续验证原游戏令牌。
- 重复服务关联被拒绝，PostgreSQL 事务完整回滚。
- 浏览器检查统一应用页面及 Application 角色列表，无控制台错误。
- 两个真实本地容器完成 Fanmade → SSO OIDC/PKCE → 回调及游戏登录；同一份非 hex
  Application 密钥用于网站和内部接口；关闭旧凭据后原网站及游戏会话仍可用。
- 新版 Fanmade 的 SSO/OIDC 针对性 Go 测试通过。

本地数据库只有合成身份，不包含生产数据。没有执行生产发布、真实邮件投递或 YataiDON 真机游玩回归。
HachiCats 应用身份的保留由合成 OAuth 应用测试覆盖；没有操作其线上网站或迁移其管理员名单。
