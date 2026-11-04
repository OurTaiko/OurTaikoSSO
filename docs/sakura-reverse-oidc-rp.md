# OurTaiko 使用 Viewer 授权登录：方案 B 首批候选

状态：**开发分支 / 默认关闭 / 未联调 / 未部署**。Viewer 1.2.4 的 OurTaiko → Viewer 功能保持不变。此候选仅建立对**已经在正式 `Sakura` Application 下完成双端绑定**的 OurTaiko 账号反向登录通路；不创建用户、不根据邮箱合并、不写新的两端关联。

## 为什么不沿用 v0.1 的外源密码接口

用户于 2026-11-04 选择由 Viewer 作为 OIDC Provider 的授权码方案 B。原来 `external/login` 会让 OurTaiko 收到用户的 Viewer 密码；对应旧 [PR #3](https://github.com/OurTaiko/OurTaikoSSO/pull/3) 和 [Viewer PR #50](https://github.com/sigaer/taiko-viewer/pull/50) 已关闭未合并。新方向以 [0.2 协议草案](https://github.com/OurTaiko/OurTaikoLogs/pull/1) 为准，不能把旧 PR 的密钥、密码认证或短期 proof 表拿回来使用。

## 已提交的 RP 部件

- `accounts/sakura_oidc_identity.py` 精确校验 Viewer `sub` 为原始十进制字符串，允许超过 JS MAX_SAFE_INTEGER，按正式 `Sakura` Application 的 `ExternalLink.external_id` 查找现有已关联 OurTaiko 账号；**不得使用 staging、邮箱、用户名或其他 Application 关系做映射**。
- `accounts/sakura_oidc_views.py`：`GET /login/sakura/` 发起浏览器授权码+PKCE S256，`GET /login/sakura/callback/` 使用受维护的 Authlib Django 客户端执行 state/nonce、Discovery/JWKS、签名、Issuer/Audience、时效验证后，仅对已有精确映射签发 OurTaiko 会话；未关联返回安全提示，不自动创建账号。
- `config/settings.py`：`SAKURA_REVERSE_OIDC_ENABLED=false` 为默认，私有 `SAKURA_REVERSE_OIDC_CLIENT_ID`、`SAKURA_REVERSE_OIDC_CLIENT_SECRET` 与现有 OurTaiko 给 Viewer 颁发的 `Sakura` 凭据互不相同。
- 登录页在开关关闭时没有新入口，现有登录行为不变。
- `accounts/tests/test_sakura_oidc_reverse.py`：精确账号、staging 隔离、无账号创建、禁止已有 Session 被替换的定向业务测试；此测试会 Mock RP，不是实际 OIDC 联调证据。

## 尚未完成，不得提前启用

1. Viewer 方必须先提供新的符合协议的 OIDC Provider：固定 `https://viewer.sakura-bot.cn/oidc` Issuer、Discovery/JWKS、授权/Token/用户资料接口、稳定十进制 `sub`、UI 明确同意、可靠存储、签名密钥及会话撤销验证。**仅有 Viewer 主站 OIDC 客户端不等于它已是 Provider**。
2. Viewer 签发独立 confidential `OurTaiko` Client ID/Secret（与现有 `Sakura` 的反方向密钥不共用），回调精确登记 `https://sso.ourtaiko.org/login/sakura/callback/`。
3. 必须通过 `uv lock` 更新 `uv.lock`，再执行 `uv export --frozen --no-dev --no-emit-project --format requirements-txt -o requirements.txt`；本 Draft 仅在 `pyproject.toml` 声明 `authlib>=1.9.2,<1.10`，**生产锁文件尚未更新**，当前不可合并部署。
4. 验证 Authlib 针对 Viewer 实际 metadata 的 OIDC 验签与 PKCE S256，并确认实际 JWKS、`sub`、cookie 和 OAuth 回调安全；服务端必须为 token/discovery/metadata HTTP 指定有界超时并禁用重定向，确保所有信任端点都来自 Viewer 固定 origin。
5. 补齐账号未关联时新用户自动注册、已有用户绑定、双端相同账号对持久恢复和 unlink 协调。这些需要在协议 v0.2 上先确认双端 link 后端证据如何由 Viewer 安全验证，不能单靠 ID Token 在 OurTaiko 写关联。
6. 全部开关保持 OFF，不传输或记录任何真实 Viewer 用户密码，生产数据库、旧 `Sakura` Application、staging 测试身份不修改。

## 最短验收口径

对已有正式精确绑定的用户，点击 OurTaiko「使用 Sakura Viewer 登录」→ 浏览器跳到 Viewer 用户登录/授权 → 回调 OurTaiko → 返回原 OurTaiko 用户、既有绑定行数不变。一次故意错误 sub、不匹配应用、失效 Session/撤销授权均拒绝。只做与这条核心链路有关的定向验证，不增加全套 CI 或灰度阶段。
