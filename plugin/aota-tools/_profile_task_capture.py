"""Bounded, redacted capture wrapper for the complete Profile Task launcher."""

from __future__ import annotations

import argparse
import os
import re
import selectors
import subprocess
import sys
from pathlib import Path


MAX_LOG_BYTES = 5 * 1024 * 1024
MAX_LINE_BYTES = 8192
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[^\s]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(api[_-]?key|token|secret|password)\s*([:=])\s*([^\s,;]+)"), r"\1\2[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED]"),
)


def redact(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _bounded_line(raw: bytes) -> str:
    if len(raw) > MAX_LINE_BYTES:
        raw = raw[:MAX_LINE_BYTES] + b"...[LINE_TRUNCATED]"
    return redact(raw.decode("utf-8", errors="replace").rstrip("\n"))


def _write_bounded(log, data: bytes, retained: int) -> int:
    if retained >= MAX_LOG_BYTES:
        return retained
    remaining = MAX_LOG_BYTES - retained
    chunk = data[:remaining]
    log.buffer.write(chunk)
    log.flush()
    return retained + len(chunk)


def run(log_path: str, command: str) -> int:
    path = Path(log_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.write_bytes(path.read_bytes()[-MAX_LOG_BYTES:])
    with path.open("a", encoding="utf-8") as log:
        retained = path.stat().st_size
        retained = min(retained, MAX_LOG_BYTES)
        retained = _write_bounded(
            log,
            b"AOTA_CAPTURE_START redaction_applied=true bounded=true\n",
            retained,
        )
        process = subprocess.Popen(
            ["/bin/bash", "-c", command],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            close_fds=True,
        )
        assert process.stdout is not None and process.stderr is not None
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            for key, _ in selector.select():
                chunk = key.fileobj.readline()
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                line = _bounded_line(chunk)
                rendered = f"[{key.data}] {line}\n".encode("utf-8", errors="replace")
                retained = _write_bounded(log, rendered, retained)
        return_code = process.wait()
        _write_bounded(log, f"AOTA_CAPTURE_END exit_code={return_code}\n".encode(), retained)
    return return_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--command", required=True)
    args = parser.parse_args()
    return run(args.log_path, args.command)


if __name__ == "__main__":
    raise SystemExit(main())
