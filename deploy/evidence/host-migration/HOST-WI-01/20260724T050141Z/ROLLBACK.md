# HOST-WI-01 rollback

No user service or remote bind was created.

1. Remove the isolated Vanilla process if one is running: `/home/latios/.venvs/hermes-agent-host/bin/hermes serve --stop` or stop its foreground process.
2. Confirm port 18642 is released.
3. Optionally remove `/home/latios/.venvs/hermes-agent-host` and the HOST-WI-01 Vanilla home after preserving evidence.
4. Preserve the source checkout for evidence.
5. Do not modify `/home/latios/.hermes`.
6. Do not restart Docker automatically. If Docker rollback is required, follow HOST-WI-00 ROLLBACK.md; never use `down -v`, prune, or volume deletion.
