# Phase 2: session-bound Sakura Viewer linking

Based on the confirmed 2026-11-04 account policy §§3.3–3.6 and the Phase 1 acceptance in Issue #2. No new identity table or database migration is introduced.

## Release controls

`SAKURA_REVERSE_LINK_ENABLED` defaults to `false`. Deploy through the existing OurTaiko Compose procedure; the authorized maintainer may enable it in the existing private SSO environment after review. Existing reverse login/signup remains enabled independently. No Viewer production credential grants administration access to OurTaiko. This change does not implement unlink.

When enabled, `/connections/` displays the Sakura Viewer link status and a CSRF-protected POST form to `/link/sakura/`. The existing registered `/login/sakura/callback/` URI is reused; no client registration or secret exchange is needed. English and Japanese strings accompany Chinese copy.

## Identity and session boundary

The link intent records the starting OurTaiko user, exact server session key, authentication hash/version, expiry and optional recovery Viewer subject, indexed by Authlib state. It is consumed once. Authlib still verifies state, nonce, PKCE, signature, issuer, audience and expiry. No OAuth token is persisted by the linking implementation.

After exchange, the original active user and live server-side session are checked again under row locks. Different-user sessions, logout/revocation, expired/replayed intents, a mismatched recovery subject or a conflicting `ourtaiko_sub` fail without granting login. The callback never replaces the OurTaiko session.

The existing formal `Sakura` application's `ExternalLink` is used. An exact pair succeeds idempotently and preserves `signup`/`partner_link` origin. A new pair gets `user_link`. Either-direction conflicts are rejected, including database uniqueness races; no existing link is overwritten or deleted.

For an unbound reverse login carrying `ourtaiko_sub`, the hint is kept for ten minutes and the notice links to normal OurTaiko login with `/connections/` as the return path. After independently signing in, the user submits the link form and completes fresh Viewer authorization. The hint cannot authenticate an OurTaiko account.

## Validation and joint acceptance

Targeted Django tests: `accounts.tests.test_sakura_link`, `accounts.tests.test_sakura_signup`, `accounts.tests.test_sakura_oidc_reverse`: 27 passed using an isolated test database. OIDC client boundary tests use mocked, already-verified claims; they are not represented as production authorization evidence.

Viewer 1.6.0 adds strict opt-in `withExternalId` parsing, duplicate-account prevention and normal Viewer login followed by fresh OurTaiko OIDC confirmation. It retains the existing mapping table and stable operation ID. Its independent delivery uses the already-live lookup/link endpoints and does not require enabling this new OurTaiko entry first.

After OurTaiko deployment, the remaining production scenario is:

1. Use dedicated accounts only: Viewer V with an existing pending/failed V↔U mapping and no OurTaiko link. Do not delete a production link to construct the test.
2. Reverse login must not create U2. Sign in normally to U, then submit the account-page linking form and authorize V.
3. Verify exactly one `user_link`, unchanged OurTaiko session, and unchanged Viewer operation ID after fresh Viewer-side confirmation becomes `linked`.
4. Repeat the same pair; verify no extra rows or origin changes. Attempt conflicting dedicated accounts; verify neither side is overwritten.

Retain the Phase 1 test accounts until coordinated cleanup. Do not publish cookies, tokens, codes, service keys or mailbox credentials. Issue #2 comments are not automatically posted by this change.
