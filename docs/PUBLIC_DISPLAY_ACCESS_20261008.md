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

Pending image build/test and controlled production replacement. Keep the previous light image for rollback; restore the original automatic-entry flag after verification. Production domain acceptance will be appended after deployment.
