# Agent Readiness Before Chat Design

## Goal

Prevent the homepage from sending a chat request while the active Agent model
has not passed the Agent decision-protocol probe, so a model switch or a stale
configuration produces an actionable readiness state instead of a post-send
generic failure.

## Scope

- The homepage performs a readiness probe before the first chat send when the
  current Agent readiness is not `ready`.
- The probe uses the authoritative server configuration when the settings form
  has not been opened or has no draft. An open, edited settings form remains the
  source for an explicit user test.
- The browser sends an empty API-key field; the server-side same-endpoint
  configuration resolver preserves the saved credential without returning it.
- A failed or unavailable probe prevents the chat payload from being sent and
  leaves the existing safe error message visible.
- The existing Agent protocol, model switch endpoint, and scientific execution
  contracts remain unchanged.
- The homepage cache-busting contract test is updated to the current script
  version already referenced by the template.

## Data flow

1. The homepage loads authoritative public Agent metadata and tracks its
   readiness locally.
2. `sendMessage` checks readiness. If it is unknown, stale, pending, or
   unavailable, it runs one bounded `/api/llm/test` probe.
3. The probe response is accepted only when it still matches the authoritative
   model/config fingerprint. A successful probe marks the Agent ready; any
   failure returns without sending the WebSocket chat payload.
4. A successful chat turn can continue to mark the unchanged saved model ready
   through the existing path.

## Error handling

- Probe failures remain actionable and do not expose credentials or provider
  response bodies.
- Concurrent model switches, form edits, and stale probe responses are ignored
  using the existing request-version and fingerprint checks.
- Existing WebSocket, docking, molecule-candidate, and scientific result paths
  are not changed.

## Verification

- Add a failing Node contract test proving an unready Agent does not send a chat
  payload and a successful readiness probe permits it.
- Update the cache-version assertion to the version in `index.html`.
- Run the homepage settings test, relevant Node tests, `node --check`, and
  Python `compileall`.
