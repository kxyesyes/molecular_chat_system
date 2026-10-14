---
name: insecure-http-host-allowlist
date: 2026-10-12
---

# Insecure HTTP Host Allowlist

## Goal

Permit the explicitly configured public host `medchat.tdd-lab.com` to serve the
protected Agent Web entry points over plain HTTP when the deployment has no TLS
termination, while preserving the current secure-by-default behavior.

## Design

- Add `MEDCHAT_INSECURE_HTTP_HOSTS`, a comma-separated list of exact hostnames.
- An HTTP or WS request is accepted only when its parsed hostname is in that
  list, or when it is one of the existing local development authorities
  (`localhost`, `127.0.0.1`, or `::1`). HTTPS/WSS behavior is unchanged.
- Matching is case-insensitive, trims surrounding whitespace, rejects empty
  entries, and does not support wildcards or URL schemes.
- The allowlist applies only to transport admission. Existing same-origin
  checks, session cookies, and WebSocket session requirements remain active.
- The default allowlist is empty, so no public HTTP host is newly trusted unless
  an operator opts in through environment configuration.

## Configuration

```env
MEDCHAT_INSECURE_HTTP_HOSTS=medchat.tdd-lab.com
```

The deployment documentation will state that this setting intentionally exposes
session cookies and application traffic to the network and should be used only
when that risk is accepted.

## Verification

Add unit coverage for default rejection, exact allowlisted-host admission,
near-match rejection, case/whitespace normalization, and WebSocket transport
admission. Run the focused session tests and the existing transport contract
tests.
