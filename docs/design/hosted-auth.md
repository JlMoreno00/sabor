# Design proposal: authentication for hosted (multi-tenant) Sabor

Status: **proposal, not implemented**. It needs a decision from the maintainer.

## Problem

Self-hosted Sabor trusts whoever can reach its HTTP endpoint: it publishes on
loopback by default and the README says to put any remote access behind an
authenticated layer. A paid hosted offer serves several households from one
process, so each MCP request must answer two questions before a tool runs:

1. **Who is calling?** (authentication)
2. **Which tenant's configuration does this call use?** (the Mealie URL and
   token, Bring credentials, nutrition database and so on)

Per-instance isolation (#3, built on the per-call context binding from #4)
answers the second question once the first one is answered: the binder can
select the tenant's `AppContext` for each call instead of using one per process.

## Constraints

- MCP over Streamable HTTP (`stateless_http=True`), FastMCP from `mcp==1.26.0`.
  The SDK already supports acting as an OAuth 2.1 **resource server**:
  `FastMCP(auth=AuthSettings(...), token_verifier=...)` validates bearer tokens
  and serves the protected resource metadata (RFC 9728) that MCP clients use
  to find the authorization server.
- The MCP authorization spec makes HTTP servers OAuth 2.1 resource servers.
  Hosted connectors (for example Claude and ChatGPT custom connectors) expect
  this OAuth flow; many CLI and desktop clients also accept a static
  `Authorization` header.
- Self-hosting must keep working with zero authentication setup on loopback.
- Public tool signatures, docstrings and schemas must not change.

## Options

### A. Static bearer token per tenant (API key)

Sabor issues each tenant a long random token. A custom `TokenVerifier` looks up
a hash of the token and returns the tenant ID.

- Pros: small (a verifier and a token table), no third party, easy to test,
  works with any client that can send a header, useful for automation and
  self-hosters who expose Sabor on a private network.
- Cons: no login, consent or MFA; users must copy and paste and rotate secrets
  by hand; revocation and expiry are ours to build; connectors that only speak
  MCP OAuth cannot use it.

### B. OAuth 2.1 resource server with a managed identity provider

Sabor validates access tokens issued by an external authorization server
(Auth0, WorkOS, Clerk, Keycloak and so on): it checks the signature (JWKS),
issuer, expiry and audience (the Sabor resource URL, RFC 8707), and maps a
tenant claim (or `sub`) to the tenant ID. The IdP handles login, consent,
MFA, refresh and dynamic client registration.

- Pros: standards-based and what hosted MCP connectors expect; real user
  login, revocation and expiry; scopes can separate read tools from write and
  confirm tools later; Sabor never stores passwords.
- Cons: dependency on and cost of an IdP, which must support the MCP client
  flows (PKCE, dynamic client registration or pre-registered clients);
  more moving parts to operate and test; tenant claims need IdP configuration.

### C. Sabor as its own OAuth authorization server

Use the SDK's `auth_server_provider` to issue tokens from Sabor itself.

- Pros: no third party.
- Cons: Sabor would own login, password and MFA storage, client registration
  and token signing. That is a large security surface for a small project.
  **Not recommended.**

### D. Authenticating gateway in front (Cloudflare Access, oauth2-proxy)

- Pros: no code; good enough for one household exposing a private instance.
- Cons: the gateway does not tell Sabor the tenant in a way MCP clients
  understand, and OAuth discovery for MCP clients does not pass through it.
  It does not solve multi-tenant hosting.

## Recommendation

Build **B for the hosted offer**, behind one small seam that also allows **A**:

1. A `TokenVerifier` returns `tenant_id` (plus scopes). The B implementation
   validates IdP JWTs; the A implementation checks hashed static tokens. Pick
   one per deployment with configuration; self-hosting keeps "no auth" on
   loopback as the default.
2. Each tool call resolves `tenant_id` → tenant `AppContext` (cached, built
   with `build_context` from that tenant's stored settings) and runs the tool
   under `use_context(tenant_context)`. This reuses the #4/#3 binding;
   registration and schemas stay unchanged.
3. Tenant secrets (Mealie token, Bring password, LLM keys) are encrypted at
   rest and never returned by tools.

Reason: paying users will mostly connect from hosted assistants that require
MCP OAuth, so A alone would block the main channel, while C is too risky.
A remains valuable for automation and private self-hosted deployments at low
cost once the verifier seam exists.

## Decisions needed from the maintainer

1. Approve B as the hosted direction (and A as the optional secondary mode)?
2. Which IdP (cost, data residency in the EU, support for MCP client
   registration)?
3. Should static tokens (A) be allowed in the hosted offer, or only for
   self-hosting?
4. Scope model: one scope for everything at first, or read/write separation
   from day one?

## Out of scope here

Billing, tenant onboarding UI, storage of per-tenant settings, rate limits and
quotas. Each needs its own issue once the authentication direction is chosen.
