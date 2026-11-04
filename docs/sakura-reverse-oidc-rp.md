# OurTaiko 使用 Viewer 授权登录：方案 B 首批候选

状态：**开发分支 / 本地隔离链路通过 / 正式未验收 / 未部署**。Viewer 1.2.4 的 OurTaiko → Viewer 功能保持不变。此候选仅建立对**已经在正式 `Sakura` Application 下完成双端绑定**的 OurTaiko 账号反向登录通路；不创建用户、不根据邮箱合并、不写新的两端关联。

## 为什么不沿用 v0.1 的外源密码接口

用户于 2026-11-04 选择由 Viewer 作为 OIDC Provider 的授权码方案 B。原来 `external/login` 会让 OurTaiko 收到用户的 Viewer 密码；对应旧 [PR #3](https://github.com/OurTaiko/OurTaikoSSO/pull/3) 和 [Viewer PR #50](https://github.com/sigaer/taiko-viewer/pull/50) 已关闭未合并。新方向以 [0.2 协议草案](https://github.com/OurTaiko/OurTaikoLogs/pull/1) 为准，不能把旧 PR 的密钥、密码认证或短期 proof 表拿回来使用。

## 已提交的 RP 部件

- `accounts/sakura_oidc_identity.py` 精确校验 Viewer `sub` 为原始十进制字符串，允许超过 JS MAX_SAFE_INTEGER，按正式 `Sakura` Application 的 `ExternalLink.external_id` 查找现有已关联 OurTaiko 账号；**不得使用 staging、邮箱、用户名或其他 Application 关系做映射**。
- `accounts/sakura_oidc_views.py`：`GET /login/sakura/` 发起浏览器授权码+PKCE S256，`GET /login/sakura/callback/` 使用受维护的 Authlib Django 客户端执行 state/nonce、Discovery/JWKS、签名、Issuer/Audience、时效验证后，仅对已有精确映射签发 OurTaiko 会话；未关联返回安全提示，不自动创建账号。
- `config/settings.py`：`SAKURA_REVERSE_OIDC_ENABLED=false` 为默认，私有 `SAKURA_REVERSE_OIDC_CLIENT_ID`、`SAKURA_REVERSE_OIDC_CLIENT_SECRET` 与现有 OurTaiko 给 Viewer 颁发的 `Sakura` 凭据互不相同。
- 登录页在开关关闭时没有新入口，现有登录行为不变。
- `accounts/tests/test_sakura_oidc_reverse.py`：精确账号、staging 隔离、无账号创建、禁止已有 Session 被替换的定向业务测试；此测试会 Mock RP，不是实际 OIDC 联调证据。

## 正式启用条件

依赖已锁为 Authlib 1.9.3，包含 `uv.lock` 与带哈希的 `requirements.txt`。实际 Django Client 子类使用受限 OAuth2Session；所有 Discovery/JWKS/Token 网络请求有 3.05 秒连接、10 秒读取超时，拒绝重定向，并固定 Viewer HTTPS `/oidc/` 来源。ID Token 算法允许列表只有 RS256，验证必需 iss/sub/aud/exp/iat/nonce；Authlib 负责 state、PKCE、nonce、签名和时效，时间容差 30 秒。

正式 Viewer Provider 必须先发布。双方还需经私有渠道配置独立 confidential `OurTaiko` Client Secret（不能复用正向 `Sakura` 密钥）；Viewer 端提供 OP 私有签名 JWKS 和持久 Cookie keys。RP 私有环境为 `SAKURA_REVERSE_OIDC_CLIENT_ID=OurTaiko`、`SAKURA_REVERSE_OIDC_CLIENT_SECRET`，正式发布后才设置 `SAKURA_REVERSE_OIDC_ENABLED=true`。

本地隔离浏览器验证不能替代正式浏览器验收；未取得生产 Discovery、JWKS、原正式绑定账号登录和重复登录证据时，PR 保持 Draft。本批不要求新用户自动注册或新的绑定协议，不把它们当成本批阻塞。

## 最短验收口径

对已有正式精确绑定的用户，点击 OurTaiko「使用 Sakura Viewer 登录」→ 浏览器跳到 Viewer 用户登录/授权 → 回调 OurTaiko → 返回原 OurTaiko 用户、既有绑定行数不变。一次故意错误 sub、不匹配应用、失效 Session/撤销授权均拒绝。只做与这条核心链路有关的定向验证，不增加全套 CI 或灰度阶段。

## 2026-11-04 验证结果

真实 Chromium 在两个固定 HTTPS origin 间跳转，使用 Viewer 新建隔离 MySQL 与 OurTaiko 新建隔离 SQLite；真实 Provider 生成授权码、Token 和 JWKS，Authlib 实际验证 RS256、issuer/audience、nonce、state、时效和 PKCE。原绑定账号两次登录成功，User/ExternalLink 各 1 行，账号对与密码/权限字段不变。未绑定同邮箱、取消、回调/授权码重放、CSRF、Viewer 会话切换、同意前及发码后撤销均通过定向检查；本仓库 6 个既有映射测试通过。

可复现入口是 Viewer PR #51 的 `taiko_viewer/scripts/verify_reverse_oidc_wire.mjs`，本仓库配套 `accounts/tests/sakura_wire_server.py` 只供隔离测试。测试没有真实生产账号、没有复制生产数据，公网证书和正式密钥交换仍需正式启用时验证。用户已确认正式反向凭据/签名配置尚未准备；当前不合并、不部署、不启用。

Viewer Nuxt 打包产物也通过相同浏览器链路与全部拒绝分支；公开生产检查确认原正向 `ourtaiko` registry 和 OurTaiko Discovery 正常，新 Viewer 反向 Discovery 仍为 404。
