# Security policy

## Reporting a vulnerability

Please **don't open a public issue** for security problems.

Report it privately through GitHub instead:
<https://github.com/KevinTechLabs/NexusLab/security/advisories/new>
(**Security → Report a vulnerability**). Include what you found, how to
reproduce it, and what an attacker could do with it. You'll get a reply within
a few days. Please allow up to 90 days for a fix before disclosing the issue
publicly.

If you spot something in this repository that looks like a real credential,
token, address or other private detail, please report it the same way.

## Supported versions

Only the latest commit on `main` is supported.

## What's already in place

- Every request to an agent needs that agent's token, and only the hub talks to the agents.
- The dashboard requires a login. Remote access is meant to go through HTTPS or a VPN such as Tailscale, not a port opened to the internet.
- Docker control is opt-in per machine (`off`, `monitor` or `control`).
