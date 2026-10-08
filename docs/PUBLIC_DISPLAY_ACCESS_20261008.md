# Public display / private administration correction - 2026-10-08

## Operator intent

The display site must work without login, including system inspection logs. Only the admin console requires login. This supersedes the initial light release's overly broad monitor authentication boundary.

## Implemented boundary

- `/`, `/decisions`, `/market-intelligence`, `/reviews`, `/trades` are public. Legacy routes keep query/hash-preserving redirects. `/admin/*` remains protected except the login screen.
- Anonymous GET/HEAD display allowlist: `/api/all`, `/api/overview`, `/api/trades`, `/api/trades/{id}`, `/api/ai/history`, `/api/ai/history/{id}`. These return the existing current-account-scoped data, not a fabricated demo fixture. A stale administrator session does not block public reads.
- The inspection log disclosure on trade records is visible to anonymous visitors. Public responses redact credential keys/values, bearer/basic authentication, credential assignments, CLI secret flags, URL user-info and private key blocks without modifying shared cached objects. Financial values are intentionally public at the operator request.
- Administration, configuration, credentials, full raw process/audit logs, raw original-prompt audit, raw caches and exchange-account command APIs remain private. The anonymous allowlist never authorizes POST/PUT/PATCH/DELETE or unknown monitor endpoints.
- Keep no-store responses and existing selected-account filtering. No risk settings, order execution, account bindings or light-mode configuration changes are included.

## Verification

- Offline backend: 1,897 tests passed, including anonymous success with absent/stale sessions, public log redaction, trade/history detail redaction, private API denial and anonymous mutation denial.
- Linux frontend: 258 tests passed, zero skips. Typecheck and production build passed.
- Edge isolated-preview functional gate: 18 checks passed, zero JavaScript errors. Coverage includes anonymous front routes and inspection logs, legacy redirects, admin login, scoped statistics, pagination, session revocation and ordinary-admin write denial.
- The isolated preview explicitly initializes an empty ledger fixture; it has no real credentials and no background trading. Real admin/financial mutations are not used for production browser verification.

## Deployment

Deployed image: `okxquant:build-2398079`. The independently built server image passed **1,897 offline tests in 349.875 seconds**, with no network or production volumes and a one-core limit. A durable locked controller paused future entries, drained active entry cycles, checked exchange protection, replaced only the app container, checked anonymous display and protected admin APIs, and restored the original automatic-entry flag `1`. The completed deployment journal and healthy new image were read back through the pinned SSH host identity. The post-restart protection check found zero positions and zero pending orders. No orders were forced closed or canceled. Previous image `5d36af2` remains available for rollback.

Production Edge verification on the operator's HTTPS domain passed **24 read-only checks**, with zero JavaScript errors. This included real anonymous overview/list/detail APIs, all canonical and legacy page routes, stale-admin-session tolerance, six private API denials, and admin navigation redirecting to login. Crucially, the browser waited for actual retained trade rows and **60 inspection log DOM entries**, rather than treating an empty loading shell as success. Desktop/mobile checks found no document-level horizontal overflow.

All browser-side non-GET/HEAD/OPTIONS traffic was blocked; attempted Cloudflare RUM telemetry was also blocked and recorded separately. No production login, settings mutation or order was used to exercise these checks. Local direct DNS returned a loopback IPv6 address, so domain verification used the existing local HTTP proxy with normal TLS verification. No DNS/hosts/proxy configuration was changed, and SSH continued to pin the independently verified server key.

Artifacts are in the ignored directory `okxquant_frontend/.ui-artifacts/public-access-20261008/`. The local disposable preview was stopped after acceptance. Documentation-only follow-up commits do not require another app restart.
