# Security policy

## Supported versions

Security fixes are provided for 0.4.x.

## Reporting a vulnerability

Please report vulnerabilities privately using [Report a vulnerability](https://github.com/JlMoreno00/sabor/security/advisories/new) in GitHub Security Advisories. If the button is unavailable, open a public issue titled "Security contact request" without any details; the maintainer will reply with a private channel. Do not disclose secrets or exploitation details in public issues.

Sabor's HTTP transport has no authentication. Keep it bound to loopback or behind a trusted, authenticated access layer; do not expose it directly to the public internet.
