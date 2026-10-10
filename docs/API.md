# SSO 内部认证协议

本地根地址 `http://127.0.0.1:8090`。以下内部接口只允许业务后端调用，不是直接提供给 YataiDON 的公开入口。

全部使用 POST JSON；要求 `X-Service-ID` 和 `X-Service-Key`，不接受浏览器 Origin，也不读取浏览器 Cookie。统一 Application 版本（本地待发布）中，头部填写 Application 的 Client ID/Client Secret，密钥由 OAuth Toolkit 哈希存储；迁移前的服务密钥仅在兼容开关开启时按原 SHA-256 哈希验证。开发用明文位于本机忽略文件 `.data/dev-clients.json`。正式环境通过私有网络或 HTTPS 调用。密钥轮换时须同步更新对应后端配置。

| 路径 | 请求 | 响应 |
|---|---|---|
| `/internal/v1/game/login` | `{username,password}` | `{user,accessToken,expiresIn}` |
| `/internal/v1/game/introspect` | `{accessToken}` | `{user,expiresAt}` |
| `/internal/v1/game/revoke` | `{accessToken}` | `{ok:true}`，重复撤销同样成功 |
| `/internal/v1/users/lookup` | `{userIds:[...]}`，最多 100 个 | `{users:[{id,nickname,avatarUrl}]}` |
| `/internal/v1/users/lookup-by-username` | `{usernames:[...]}`，最多 100 个 | `{users:[{id,username,nickname,avatarUrl}]}` |

## 登录

成功 200：

```json
{
  "user": {
    "id": "11111111111111111111111111111111",
    "username": "player1",
    "nickname": "玩家",
    "emailVerified": true,
    "isAdmin": false
  },
  "accessToken": "64位小写十六进制随机字符串",
  "expiresIn": 604800
}
```

token 是不可解析的随机凭证，不是 JWT，仅按哈希持久化。有效期 7 天，绑定调用的服务。Fanmade token 不能由 ESE 验证或使用。`isAdmin` 只代表对应业务应用的角色，SSO 管理员身份不赋予其他应用的权限。

原业务后端代理 `/api/v1/game/login` 时，返回值保持此形状；客户端无需了解 SSO。用户密码在认证时透传，不应缓存、落库、记录请求体或写日志。

## 鉴权与资料

业务后端将玩家传入的 Bearer token 作为 `accessToken` 发给 introspect。仅在成功后使用返回的 ID。昵称及角色每次由 SSO 当前数据库读取；密码修改、重置、用户停用、会话撤销会使旧游戏 token 失效。

为了立即生效，本版本不缓存鉴权结果。调用方若自行缓存，必须接受相应的撤销延迟。SSO 不可达时，业务后端返回可重试的 503；不能把客户端传来的 ID 当作备用身份，也不要将上游故障伪装成密码错误。游客曲库由业务端独立提供。

`users/lookup` 仅按 ID 查询，保持原输入和响应。独立的 `users/lookup-by-username` 按精确区分大小写的 username 查询，并返回 username 以供关联。两个接口均拒绝另一种查询字段及混合输入，用户名需 1–24 字符且无空白。两者都要求 Application 的资料查询权限，不返回角色、邮箱或其他身份信息，也不能用来授权。新增用户名接口需部署后才会生效。

lookup 只返回有效用户的 ID、昵称和头像地址（未设置头像时 `avatarUrl` 为空字符串）；不存在/停用用户省略。调用方应为缺失条目提供显示占位，不能因此删除成绩。公开昵称可短暂缓存，建议 30–60 秒；不在业务数据库建立另一份权威用户资料。

## 错误

JSON 错误格式 `{code,message}`。旧 Fanmade 网关可补充它自己的 `requestId` 和 `validationVersion`。

| HTTP | code | 含义 |
|---|---|---|
| 400 | REQUEST_INVALID | JSON、字段或 ID 列表无效 |
| 401 | SERVICE_UNAUTHORIZED | 业务服务密钥错误或服务停用 |
| 401 | LOGIN_INVALID | 登录凭证错误、账号未激活或停用 |
| 401 | UNAUTHORIZED | 游戏 token 过期、撤销或服务不匹配 |
| 403 | SERVICE_FORBIDDEN | Application 未获准使用该类内部接口 |
| 403 | ORIGIN_INVALID | 浏览器不能直接调用内部接口 |
| 405 | METHOD_NOT_ALLOWED | 使用了 POST 以外的方法 |
| 415 | CONTENT_TYPE_INVALID | 非 JSON 请求 |
| 429 | RATE_LIMITED | 账号或来源登录限流，附 Retry-After |

HTTP 请求体上限 32 KiB。业务端使用连接/读取超时，禁止将 token、密钥放入 URL，日志隐藏 Authorization、X-Service-Key 和密码。身份失败与上游网络失败分开处理。

## 网页 API

网页通过 Django session Cookie 和 CSRF 表单操作，Cookie 名为 `ourtaiko_sso_session`，不与 Fanmade 原 Cookie 混用。修改个人资料只能修改 nickname。登录名、邮箱和用户 ID 目前只读。

OIDC 端点由发现文档提供，scope 为 `openid profile`。标准登录使用授权码 + PKCE；不开放 OAuth 密码授权来替代游戏内部接口。游戏 token 与 OIDC token 不可互换。

## Fanmade 网站会话接入

统一版本直接使用调用方 Application 验证网站 token 的归属，不再另建 GameClient 或配置 web_client_id。应用必须是 confidential，且开启网站令牌接口权限；迁移过程见 [APPLICATION-MIGRATION.md](APPLICATION-MIGRATION.md)。

- `POST /internal/v1/web/introspect`：`{accessToken}`；返回与游戏身份查询一致的 `{user, expiresAt}`。只接受绑定应用的有效 OAuth access token，拒绝其他业务令牌和游戏令牌。isAdmin 取对应 Application 的 ClientRole。
- `POST /internal/v1/web/revoke`：`{accessToken}`；撤销本应用网站 access token，重复调用仍返回 `{ok:true}`。
- `POST /internal/v1/users/search`：`{query, after?}`；按昵称包含文本搜索，返回 `{userIds,next}`，每页最多 1000 条，next 为空表示结束；不检索登录名或邮箱。

以上均要求既有 X-Service-ID / X-Service-Key，拒绝浏览器 Origin，不开放跨域调用。

授权页 CSP 的 form-action 只额外允许当前应用已登记 redirect_uri 的来源，用于跨站 POST 授权后的浏览器回跳。

## Preferred language

The SSO user record stores `preferred_language`, one of `zh-hans` (default), `en`, `ja`, or `ko`. Migration `0003_user_preferred_language` assigns Chinese to existing accounts. Authenticated identity responses (`game/login`, `game/introspect`, `web/introspect`) include `user.preferredLanguage`. OIDC exposes the standard `locale` claim only under the `profile` scope. Public nickname lookup/search do not expose preferences.

The account profile form and the CSRF-protected `POST /language/` endpoint update the preference. The endpoint accepts form fields `language` and `next`; unsupported languages are rejected and redirects are limited to the same host. For anonymous visitors, explicit language selection is kept in the SSO session, and registration copies it into the new account. Existing account preferences take priority on login. Without a preference, the website uses Chinese; it does not infer a language from `Accept-Language`.

Business services relay live identity data rather than storing a second preference. Changing language does not change passwords, IDs, roles or authentication versions and does not revoke sessions.

## Avatars

Users upload JPG/JPEG/PNG/WebP files (≤ 5 MB, ≤ 40 MP) on the profile page (`POST /profile/avatar/`, multipart, CSRF-protected; `action=remove` clears it). The SSO decodes only the format matching the extension, applies EXIF orientation, keeps the first frame, center-crops to a square of at most 512 px, strips metadata and re-encodes to WebP. Objects are stored in the S3 bucket at `sso/avatars/<user id>/<sha256 prefix>.webp`; the previous object is deleted after the new one is committed. `User.avatar` holds the name relative to the `sso/` prefix.

Avatars are public, like nicknames. They are served by the SSO at `GET /avatars/<user id>/<hash>.webp` (the bucket can stay private); the URL is content-addressed, so responses use `Cache-Control: public, max-age=31536000, immutable`, and a new upload yields a new URL. Inactive users' avatars return 404.

`avatarUrl` (absolute, empty when unset) appears in `users/lookup` and in the `user` object of `game/login`, `game/introspect` and `web/introspect`. OIDC exposes the standard `picture` claim under the `profile` scope, omitted when unset. Callers should cache by URL and fall back to an initial/placeholder when it is empty or fails to load.
