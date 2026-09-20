# Production deployment

The SSO container is managed by **1Panel → Containers → Compose → ourtaiko-sso**.
It runs the production Gunicorn image `ourtaiko-sso:release`, with a read-only
filesystem and a non-root user, listening on `127.0.0.1:8090`.

1Panel also owns a separate reverse-proxy website named `ourtaiko-sso`:
`https://fanmade.ourtaiko.org:8443`, using the existing Fanmade certificate.
The proxy sends `Host: fanmade.ourtaiko.org:8443` and overwrites the forwarded
scheme and client address. The operating-system firewall permits TCP 8443.
Public TCP 8443 was opened at the provider on 2026-09-20. External HTTPS,
OIDC discovery and the complete browser/game flow are now verified. Fanmade
uses this SSO issuer in production; its own public API remains on HTTPS 443.

## Server files

- Source checkout: `/opt/ourtaiko-sso/src`
- 1Panel compose source: `/opt/ourtaiko-sso/docker-compose.yml`
- Environment: `/etc/ourtaiko-sso/sso.env` (root-only)
- Persistent signing key: `/var/lib/ourtaiko-sso/oidc-private.pem`
- Separate database and non-superuser role: `ourtaiko_sso`
- Prepared Fanmade environment: `/etc/ourtaiko-sso/fanmade-next.env`
- Read-only import connection: `/etc/ourtaiko-sso/import.env`
- Prepared new backend binary: `/tmp/ourtaiko-api-sso` (rebuild if absent)

SMTP uses the existing production mail service. TLS authentication was checked
without sending mail.

## Verified release (2026-09-20)

- Backend `5492b09` and frontend `50bde22` are pushed to `main` and live.
- SSO image is built from `5a54ce7`; later SSO commits update this runbook only.
- [Frontend deployment workflow](https://github.com/OurTaiko/Fanmade_Frontend/actions/runs/35542581510)
  passed dependency install, lint, tests, build and SSH publishing.
- All 7 legacy IDs, usernames, nicknames, bcrypt hashes, email/verification flags
  and Fanmade admin roles were compared before migration. No profile fields or
  passwords remain in Fanmade's `users` table: it contains only `id`.
- Business schema is 18. Final counts remain 7 users, 26 charts and 48 scores;
  the separate SSO database contains the same 7 users.
- Production Chromium verification passed real frontend login → SSO consent →
  OIDC/PKCE callback, legacy bcrypt login, account-center navigation, chart upload,
  game login without cookies, score storage and idempotent retries, nickname
  propagation to web/game/chart/leaderboard, password-change revocation of both
  session types, old-password rejection, re-login and logout.
- The test used a disposable account, chart and score, with automatic cleanup in
  `finally`. All temporary records were removed; the durable file-deletion queue
  was verified empty afterwards. No verification mail was sent.
- Existing browser/game sessions require login again with the original account.

Final pre-migration backup (API stopped before dump):
`/var/backups/ourtaiko-sso-migration/20260920T224353Z`.
This includes the original database/files, old backend binary/environment,
frontend release target, and the successful account-import comparison report.

Validated post-migration backup of both databases, application files, private
configuration and signing key:
`/var/backups/ourtaiko-sso-migration/post-20260920T225108Z`.
Keep both backup directories root-only; they contain secrets.

## Cutover and rollback runbook

1. Verify the public SSO URL, trusted certificate, `/readyz`, login page and OIDC
   discovery from an external client. If changing the hostname, update SSO base
   URL/allowed hosts, Fanmade issuer, proxy Host, and certificate together.
2. Stop `ourtaiko-fanmade` to freeze legacy account writes, and create a **fresh**
   database dump plus application/configuration backup. The original cutover used the final pre-migration backup documented above.
   For any later migration, take another fresh backup.
3. Run `import_fanmade` dry-run and `--apply` with both SSO and import environment
   files. Verify every source ID, username, nickname, bcrypt hash, email,
   verification flag and admin role before dropping legacy account columns.
4. Install the prepared Fanmade environment with the same root:ourtaiko owner
   and mode 0640 as the old environment, install the new backend binary, then
   start `ourtaiko-fanmade`. Migration 018 checks all existing IDs against SSO
   before committing. Verify `/readyz`, charts and score ownership/counts.
5. Advance frontend `main` to the verified SSO commit and push. Watch the existing
   GitHub Actions deploy job and confirm the new 1Panel release symlink.
6. Verify browser OIDC callback/logout and account-center links, plus legacy
   game login/token validation/score operations. Use a disposable test account
   and remove only its data afterwards. Old sessions require login again.
7. Back up both final databases and retain the private configuration/signing key.

If migration fails, keep the frontend on its old release. Restore the old backend
binary and environment; if migration 018 committed, restore the fresh pre-cutover
Fanmade dump while the API is stopped, before restarting the old binary. Keep
SSO data and all original backups until rollback has been verified.
