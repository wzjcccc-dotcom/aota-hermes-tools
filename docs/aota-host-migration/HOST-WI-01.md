# HOST-WI-01 — Vanilla Hermes Host Installation

## Current result

`FINAL=NEEDS_OPERATOR_PROVIDER_AUTH`

Host installation and localhost protocol smoke are complete. Remote bind, user service, Windows Desktop validation, and restart persistence are deliberately pending because the isolated Vanilla runtime has no provider credential/model.

## Selected version and topology

- Docker baseline: `nousresearch/hermes-agent:latest`, image ID `sha256:ad2c82eeee5295ef919b970839c84ad924b93fa89883a434c3f800c1479e8105`, OCI revision `8a21df18acbe73c63d06747d0ab359288bf84276`.
- Hermes metadata: `0.19.0`, release date `2026.7.20`; official release line `v2026.7.20`.
- Source: `/home/latios/workspace/hermes-agent-host`, detached at the exact image revision.
- Venv: `/home/latios/.venvs/hermes-agent-host`.
- Vanilla home: `/home/latios/.local/state/aota-host-migration/HOST-WI-01/vanilla-hermes-home`, mode 700.
- Installation: official source checkout with `uv` editable install and `[web,pty]` extras.

The existing `/home/latios/.hermes` was not used as the Vanilla runtime. A single accidental update-cache write from an unisolated version probe was restored byte-for-byte from the HOST-WI-00 private backup; subsequent probes used a clean environment with explicit `HERMES_HOME`.

## Verified host-side gates

`hermes serve --host 127.0.0.1 --port 18642 --skip-build` started successfully in a controlled foreground process. `/api/status` reported the isolated home and Hermes `0.19.0`; sensitive API access without the test session token returned 401. The official `/api/ws` JSON-RPC handshake returned `gateway.ready`, and `session.create`/`session.list` returned valid results.

Provider readiness is not complete: the isolated home has no configured provider/model and the environment exposed no provider credential key. Therefore no chat, durable chat session, remote bind, auth probe, systemd unit, or Desktop validation is claimed.

## Operator checkpoint

Before the next host-side continuation, configure one official Hermes provider inside the Vanilla home using the official interactive auth/setup flow. Do not copy the canonical `/home/latios/.hermes/auth.json`, Docker `.env`, AOTA Proxy credentials, or any token. Report only whether provider setup completed and the provider name; never paste credentials.

After provider readiness, continue in order: localhost fixed-response chat and restart persistence; then configure official dashboard auth, verify Tailscale IP and user bus, create but do not enable the user service, and produce the Desktop connection checkpoint. Codex must not claim Desktop live results without operator confirmation.

## Rollback and handoff

The timestamped evidence is under `deploy/evidence/host-migration/HOST-WI-01/20260724T050141Z/`. No systemd unit, remote bind, Docker change, AOTA plugin/profile, or `hermes-overrides` change was made. `HOST-WI-02` is not started.
