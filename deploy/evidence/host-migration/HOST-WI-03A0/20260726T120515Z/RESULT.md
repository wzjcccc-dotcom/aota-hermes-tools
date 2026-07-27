# HOST-WI-03A0 Result

Status: `PASS_RECONNAISSANCE_WITH_DECISION`

Decision: `AUTOMATIC_PARENT_WAKEUP_NOT_SUPPORTED` for the current AOTA Profile Task → official Hermes Desktop path. Operationally this is **D now**: task-main reads the durable completion receipt/meta/handoff/result artifacts. If automatic parent wake is later required, the smallest missing boundary is **C**: a narrowly scoped trusted backend delivery hook, likely fed by the existing sanitized AOTA outbox. A direct native API (**A**) is not proven compatible because the Profile Task is a separate one-shot `hermes -z` process and does not enter Hermes `async_delegation` or `gateway.wake`.

## Executive finding

Hermes native background delegation is implemented and durable for native `delegate_task`/subagent work. It persists an `async_delegations` row, queues a completion event, claims it in CLI/gateway, and delivers a synthetic new turn to a still-valid parent/session target. It is at-least-once and can restore undelivered completions after the Hermes backend restarts.

The Desktop layer is a WebSocket/JSON-RPC client plus an SSH process/tunnel lifecycle manager. It reconnects to the backend and can observe persisted session messages, but the inspected client has no durable event cursor or missed-event replay handshake. SSH session token/owner nonce provide remote backend ownership/reuse, not parent completion replay.

AOTA Profile Task has its own durable chain: worker process → completion receipt → meta status → handoff → sanitized filesystem outbox. `_delivery_adapter.py` is designed to drain that outbox to a WebUI `SessionChannel/SSE` boundary, but `P11-L.1B-Delivery-Bridge.md` explicitly defers actual WebUI integration, reconnect handling and end-to-end validation. No current path was found from that adapter to official Hermes Desktop parent-session message append or automatic model resume.

## 25 core answers

1. **Can Desktop start a background child while the main session waits?** Native Hermes: yes for native `delegate_task` on an eligible persistent channel. AOTA Profile Task: it can start a separate worker, but this is not the native Desktop delivery path.
2. **Can the child continue after the parent turn finishes generating?** Native: yes while the owning process/session remains valid. Closing/resetting the session or process ends the native lifecycle; a running child is not resumed after crash. AOTA launcher uses a separate process session and can continue independently.
3. **How does completion return?** Native: durable `async_delegations` row → shared completion queue → CLI/gateway claim → synthetic new session turn. AOTA: receipt/meta/handoff/outbox; no proven Desktop parent route.
4. **Identity relation?** Native has `parent_session_id`, `origin_session_id`, `session_key`, and delegation identity; no dedicated `root_session_id` in the observed schema. AOTA `task_id`/`process_session_id` are not native child-session IDs.
5. **Completion event?** Native: yes, `type=async_delegation` event. AOTA: yes as a filesystem outbox event, not a Hermes gateway event.
6. **Message injection into parent?** Native: synthetic new turn, not historical-context splicing. AOTA: no parent message append found.
7. **Background queue drain?** Native: yes, gateway watcher and CLI drain. AOTA: filesystem pending-event drain exists, but current Desktop integration is not proven.
8. **Reconnect replay?** Native backend: durable undelivered-completion restore. Desktop client: no event-cursor replay. AOTA adapter: reconnect drain is documented as deferred.
9. **Desktop offline persistence?** Yes only when the remote/backend process remains able to persist native completion or AOTA outbox; Desktop itself is not the durable ledger.
10. **Desktop reconnect補送?** No direct Desktop event replay is evidenced. Persisted parent messages may become visible after session reload if backend already processed the completion.
11. **`supports_async_delivery=true` required?** For native push delivery, yes as a channel capability. Stateless API/one-shot paths are false; API has a special raw-session self-POST fallback.
12. **Which transports support it?** CLI interactive and persistent gateway adapters. API server only through the special self-POST path. One-shot `hermes -z` does not. Desktop is a client, not the flag-owning adapter.
13. **Does Desktop SSH backend support it?** Indirectly through the remote Hermes backend; SSH itself supplies process/tunnel persistence, not delivery/replay.
14. **Does CLI support it?** Yes for interactive CLI queue ownership/drain; no for one-shot semantics.
15. **Does API server support it?** Not as a persistent push adapter; native delegate can self-POST to the raw bound session ID.
16. **Do subagent and delegate share a path?** Native background delegate/subagent completion uses the same async delegation queue and gateway watcher. AOTA Profile Task does not.
17. **What is notification?** Native: durable database row + completion event + synthetic session message/new turn + normal UI stream. AOTA: receipt/meta/handoff + sanitized filesystem outbox; no proven Desktop event or model resume.
18. **Cancellation/timeout/failure?** Native terminal/error/interrupted/unknown states and interrupt/recovery paths exist. AOTA finalizer records done/failed/needs_input/cancelled/timeout/scope outcomes and emits recognized terminal outbox events.
19. **Exactly-once or at-least-once?** Native and AOTA bridge are at-least-once-oriented; claims/atomic rename/idempotency reduce duplicates but do not establish end-to-end exactly-once.
20. **Can completion duplicate?** Yes around crash or ambiguous acceptance; native stale claims/retries and AOTA pending outbox retry make this explicit.
21. **Missing/deleted/closed parent?** Native gateway fails closed and drops injection for invalid/ended targets; compression has a verified continuation path. AOTA artifacts remain readable, but no parent wake exists to handle the missing target.
22. **After profile switch where does it go?** Native completion remains associated with its origin profile/session; it is not moved to the newly selected profile.
23. **Can named profile DBs cross-report?** No native cross-profile route was found. Each profile has separate `state.db`, sessions and messages.
24. **Can AOTA Profile Task share native mechanism?** Not directly: it is a separate one-shot subprocess, has a filesystem receipt/outbox, and lacks native queue/wake calls.
25. **Smallest adapter boundary?** Current safe boundary is the existing AOTA finalizer outbox, followed by a trusted current backend/WebUI adapter that validates origin/profile ownership and emits a bounded event. If the requirement is automatic LLM parent resume rather than UI notification, this becomes a minimal Hermes backend hook; absent that, task-main reads the result.

## Safety and scope

No Hermes source, Desktop source, AOTA source, profile, skill, launcher, private environment, overlay, runtime database or state.db was modified. Docker, Profile Task, `delegate_task`, real background work, CodeGraph mutation, AMF mutation, commit and push were not performed. The runner `/usr/local/bin/hermes` Docker-guard failure remains the separate HOST-WI-02C0 issue.

Evidence files in this directory contain no API keys, OAuth tokens, SSH tokens, owner nonces, raw session IDs, session content, tool arguments or private environment values.
