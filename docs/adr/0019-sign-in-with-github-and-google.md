# ADR-0019: Sign in with GitHub, then Google; one tenant per account

- Status: Accepted
- Date: 2026-11-16
- Deciders: Jian (GitHub first, Google as a secondary option), Claude

## Context

Hosted use needs real accounts (GEO-50). Until now a viewer types an API key that the API maps to a tenant. indah does not log anyone in; it asks a parent app who a request is (`auth=`, ADR-0033 of indah) and, with `secure=True`, refuses sessions without an identity and keeps one user out of another's session.

## Decision

- **OAuth 2.0 authorization-code login in front of the web UI** (`geopipe/signin.py`, wrapped around indah's ASGI app in `stages/frontend/app.py`). GitHub first, Google second; the login page shows only the providers that are configured (`GEOPIPE_GITHUB_CLIENT_ID` and `_SECRET`, same for `GOOGLE`). With none configured the UI works as before and asks for an API key, so development and CI need no provider.
- **Unauthenticated visitors are redirected** to `/login` (a 401 on `/api/`, a refused WebSocket). indah runs with `secure=True`, so every session is owned by one account.
- **One tenant per account**: `github-<numeric id>` or `google-<sub>`, from the provider's permanent id and never from a name or an email, so renaming an account keeps its data and nobody can take over a name. `shared` can never be a tenant.
- **The API learns the tenant from a signed token**, not from the provider. After login the frontend sets an HttpOnly, SameSite=Lax cookie (a token signed with `GEOPIPE_SESSION_SECRET`, seven days), and for each API call mints a five-minute `Authorization: Bearer` token naming the tenant. The API verifies it with the same secret (`geopipe/identity.py`, HMAC-SHA256 with an expiry) and API keys keep working beside it, for scripts.
- **The state parameter** is a random value in a signed ten-minute cookie bound to the provider; a callback without a matching state is refused. Provider replies are never logged.
- **Google uses the userinfo endpoint** with the access token obtained over TLS straight from Google; the ID token is not parsed, so no JWT library or key rotation to handle. GitHub is OAuth 2.0 only (no OIDC), which the same flow covers.
- Secrets live in the environment (compose passes them through; the dev session secret in compose is for development only and must be replaced with at least 32 random bytes).

## Setting up a provider

- GitHub: Settings, Developer settings, OAuth Apps, New. Callback URL `<GEOPIPE_PUBLIC_URL>/auth/github/callback`.
- Google: Cloud console, APIs and Services, Credentials, OAuth client ID (web). Authorised redirect URI `<GEOPIPE_PUBLIC_URL>/auth/google/callback`.
- `GEOPIPE_PUBLIC_URL` is the address browsers use (default `http://localhost:8080`); `https://` turns the Secure flag on cookies.

## Alternatives considered

| Option | Why not |
|---|---|
| The API runs the login | It would need to be reachable by browsers and own cookies; the frontend already is the browser-facing server |
| Pass the provider's access token to the API | The API would call GitHub or Google on every request and know about both |
| An auth library (Authlib) | Two providers and about 150 lines; one more dependency to track |
| Keep API keys only | Not an account system |

## Consequences

- Anyone with a GitHub or Google account can create a tenant. Per-tenant quotas (ADR-0017) limit the damage; an allow-list is a small addition if needed.
- Logging out clears the cookie but cannot revoke a token already minted; those last at most five minutes.
- Rotating `GEOPIPE_SESSION_SECRET` signs everyone out.
- Not covered by tests: the real providers (the flow is tested with faked provider replies, and the redirect URLs must be set in each provider's console by hand).
