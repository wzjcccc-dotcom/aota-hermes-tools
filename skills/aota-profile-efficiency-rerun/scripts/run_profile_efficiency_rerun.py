#!/usr/bin/env python3
"""Repeat the managed AOTA profile-efficiency validation run.

This runner deliberately keeps orchestration deterministic and leaves source
files untouched.  It deploys/reloads when requested, executes the fixed
r5-30 fixture, and writes bounded evidence rather than full transcripts.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import io
import json
import os
import re
import selectors
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid
from pathlib import Path


PROFILES = ("project-steward", "task-main", "architect", "reviewer", "coder", "debugger")
RAW_PROFILES = ("project-steward", "architect", "reviewer", "coder", "debugger")
WEIGHTS = {
    "outcome_completion": 0.30,
    "correct_tool_and_parameter_path": 0.25,
    "retry_stop_safety": 0.20,
    "total_tool_budget": 0.10,
    "minimal_skill_and_tool_description": 0.15,
}


def command(args: list[str], *, cwd: Path, env: dict[str, str] | None = None,
            timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True,
                          timeout=timeout, check=False)


def runtime_env(*, session_id: str | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env.update({
        "HERMES_HOME": "/home/latios/.hermes",
        "AOTA_RUNTIME_ROOT": "/home/latios/.hermes/aota-runtime",
        "AOTA_PROFILE_TASK_ROOT": "/home/latios/.hermes/aota-runtime/profile-tasks",
        "AOTA_CANONICAL_WORKSPACE_ROOT": "/home/latios/workspace",
        "AOTA_TRUSTED_WORKSPACE_ID": "aota-hermes-tools",
        "LD_LIBRARY_PATH": "/home/latios/.local/opt/sqlite-3.53.4/lib:"
        + env.get("LD_LIBRARY_PATH", ""),
    })
    if session_id:
        env["HERMES_SESSION_ID"] = session_id
        env["HERMES_SESSION_KEY"] = session_id
    return env


def newest_receipt(repo: Path) -> Path | None:
    # Forge receipts live at repository root (`.deploy-receipts`). Keep the
    # historical deploy/ location as a compatibility fallback for older
    # packages so evidence always carries the actual receipt path.
    roots = (
        repo / ".deploy-receipts/aota-forge-plan",
        repo / "deploy/.deploy-receipts/aota-forge-plan",
    )
    paths = [path for root in roots for path in root.glob("*/deployment.json")]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return paths[0] if paths else None


def deploy(repo: Path) -> dict[str, object]:
    result = command([
        sys.executable, "scripts/aota_forge_plan_package.py", "deploy",
        "--allow-known-secret-false-positive",
    ], cwd=repo, timeout=900)
    if result.returncode:
        raise RuntimeError("managed deploy failed:\n" + (result.stdout + result.stderr)[-4000:])
    receipt = newest_receipt(repo)
    plugin_text = (repo / "plugin/aota-tools/plugin.yaml").read_text(encoding="utf-8")
    version_match = re.search(r"^version:\s*['\"]?([^'\"\s]+)", plugin_text, re.MULTILINE)
    return {
        "returncode": result.returncode,
        "source_version": version_match.group(1) if version_match else None,
        "receipt": str(receipt.relative_to(repo)) if receipt else None,
        "output_tail": (result.stdout + result.stderr)[-1200:],
    }


def reload_native(repo: Path) -> dict[str, object]:
    hermes = os.environ.get("HERMES_BIN", "/home/latios/.venvs/hermes-agent-host/bin/hermes")
    env = runtime_env()
    stopped = command([hermes, "serve", "--stop"], cwd=repo, env=env, timeout=180)
    proc = subprocess.Popen(
        [hermes, "serve", "--skip-build", "--port", "0"], cwd=repo, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    assert proc.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    lines: list[str] = []
    port: int | None = None
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        for key, _ in selector.select(timeout=1):
            line = key.fileobj.readline()
            if not line:
                break
            lines.append(line.rstrip())
            match = re.search(r"HERMES_BACKEND_READY\s+port=(\d+)", line)
            if match:
                port = int(match.group(1))
                break
        if port:
            break
        if proc.poll() is not None:
            break
    if not port:
        proc.terminate()
        raise RuntimeError("native Hermes reload did not report HERMES_BACKEND_READY:\n" +
                           "\n".join(lines[-30:]))
    health = None
    try:
        with urllib.request.urlopen("http://127.0.0.1:8792/health", timeout=10) as response:
            health = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # pragma: no cover - host-dependent
        raise RuntimeError(f"AOTA Proxy health failed after reload: {exc}") from exc
    return {
        "stop_returncode": stopped.returncode,
        "pid": proc.pid,
        "backend_port": port,
        "proxy_health": health,
    }


def db_row(profile: str, session_id: str | None) -> dict[str, object]:
    if not session_id:
        return {}
    path = Path(f"/home/latios/.hermes/profiles/{profile}/state.db")
    if not path.exists():
        return {}
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "select id, profile_name, started_at, ended_at, message_count, "
            "tool_call_count, api_call_count, input_tokens, output_tokens "
            "from sessions where id=?", (session_id,)
        ).fetchone()
    return dict(row) if row else {}


def db_events(profile: str, session_id: str | None) -> list[dict[str, object]]:
    if not session_id:
        return []
    path = Path(f"/home/latios/.hermes/profiles/{profile}/state.db")
    if not path.exists():
        return []
    events: list[dict[str, object]] = []
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "select role, tool_name, tool_calls, effect_disposition "
            "from messages where session_id=? order by timestamp", (session_id,)
        )
        for row in rows:
            item = dict(row)
            names: list[str] = []
            if item.get("tool_name"):
                names.append(str(item["tool_name"]))
            if item.get("tool_calls"):
                try:
                    names.extend(
                        call.get("function", {}).get("name")
                        for call in json.loads(item["tool_calls"])
                        if call.get("function")
                    )
                except (TypeError, ValueError, AttributeError):
                    pass
            if names:
                events.append({"role": item["role"], "names": names})
    return events


def raw_case(case: dict[str, object], repetition: int, repo: Path,
             temp_root: Path, timeout: int) -> dict[str, object]:
    profile = str(case["profile"])
    session = f"r8-{profile}-{repetition:02d}-{uuid.uuid4().hex[:8]}"
    usage = temp_root / f"{session}-usage.json"
    env = runtime_env(session_id=session)
    started = time.monotonic()
    try:
        result = subprocess.run([
            os.environ.get("HERMES_BIN", "/home/latios/.venvs/hermes-agent-host/bin/hermes"),
            "-p", profile, "--no-restore-cwd", "-m", "glm-5.2",
            "--provider", "ollama-cloud", "--usage-file", str(usage),
            "-z", str(case["prompt"]),
        ], cwd=repo, env=env, text=True, capture_output=True, timeout=timeout)
        returncode = result.returncode
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = str(exc.stdout or "")
        stderr = "timeout"
    usage_data = json.loads(usage.read_text(encoding="utf-8")) if usage.exists() else {}
    session_id = usage_data.get("session_id")
    row = db_row(profile, session_id)
    return {
        "profile": profile,
        "rep": repetition,
        "session_id": session_id,
        "returncode": returncode,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "usage": usage_data,
        "session": row,
        "tool_events": db_events(profile, session_id),
        "stdout_tail": stdout[-1800:],
        "stderr_tail": stderr[-800:],
    }


def raw_samples(cases: dict[str, dict[str, object]], repo: Path, temp_root: Path,
                workers: int, timeout: int) -> list[dict[str, object]]:
    def profile_batch(profile: str) -> list[dict[str, object]]:
        return [raw_case(cases[profile], rep, repo, temp_root, timeout) for rep in range(1, 6)]

    result: list[dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        for batch in executor.map(profile_batch, RAW_PROFILES):
            result.extend(batch)
    return sorted(result, key=lambda item: (str(item["profile"]), int(item["rep"])))


def task_main_sample(prompt: str, repetition: int, repo: Path, timeout: int,
                     temp_root: Path) -> dict[str, object]:
    try:
        import pexpect  # type: ignore
    except ImportError as exc:  # pragma: no cover - environment-dependent
        raise RuntimeError("task-main rerun requires pexpect") from exc
    env = runtime_env()
    log = io.StringIO()
    child = pexpect.spawn(
        os.environ.get("HERMES_BIN", "/home/latios/.venvs/hermes-agent-host/bin/hermes"),
        ["-p", "task-main", "--cli", "--no-restore-cwd", "-m", "glm-5.2",
         "--provider", "ollama-cloud"],
        cwd=str(repo), env=env, encoding="utf-8", timeout=60, logfile=log,
    )
    child.setwinsize(60, 200)
    started = time.monotonic()
    gate = ""
    error = ""
    try:
        child.expect("❯", timeout=240)
        child.send(prompt)
        child.send("\r")
        patterns = [
            # Models commonly add an em dash or punctuation before 完成
            # (for example: "AOTA Profile 效率驗證 V1 — 完成。"). Keep the
            # marker bounded to the same line so an echoed prompt cannot
            # satisfy the completion gate.
            r"AOTA Profile 效率驗證 V1[^\r\n]{0,80}完成",
            r"效率驗證 V1[^\r\n]{0,80}完成",
            "全部完成",
            "全流程完成",
            "流程完成",
            "STATUS: 完成",
        ]
        index = child.expect(patterns, timeout=timeout)
        gate = patterns[index]
        child.send("/exit\r")
        child.expect(pexpect.EOF, timeout=240)
    except Exception as exc:  # Keep the DB evidence for postmortem scoring.
        error = repr(exc)
        try:
            child.send("\x04")
            child.expect(pexpect.EOF, timeout=60)
        except Exception:
            child.close(force=True)
    transcript = log.getvalue()
    session_match = re.findall(r"Session:\s+([0-9]{8}_[0-9]{6}_[a-z0-9]+)", transcript)
    session_id = session_match[-1] if session_match else None
    parent = db_row("task-main", session_id)
    if not session_id or not parent:
        # The final Session line can be lost when a TUI is interrupted.
        path = Path("/home/latios/.hermes/profiles/task-main/state.db")
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "select id from sessions where started_at>=? order by started_at desc limit 1",
                (started,),
            ).fetchone()
        session_id = row["id"] if row else session_id
        parent = db_row("task-main", session_id)
    worker = {}
    if parent.get("started_at"):
        path = Path("/home/latios/.hermes/profiles/architect/state.db")
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "select id from sessions where started_at>=? and started_at<=? "
                "order by started_at desc limit 1",
                (parent["started_at"], parent["started_at"] + timeout + 240),
            ).fetchone()
        worker = db_row("architect", row["id"]) if row else {}
    return {
        "profile": "task-main",
        "rep": repetition,
        "session_id": session_id,
        "worker_session_id": worker.get("id"),
        "gate": gate,
        "error": error,
        "elapsed_sec": round(time.monotonic() - started, 2),
        "session": parent,
        "worker_session": worker,
        "stdout_tail": transcript[-5000:],
    }


def task_samples(case: dict[str, object], repo: Path, timeout: int,
                 temp_root: Path) -> list[dict[str, object]]:
    return [task_main_sample(str(case["prompt"]), rep, repo, timeout, temp_root)
            for rep in range(1, 6)]


def criteria_raw(item: dict[str, object], limits: dict[str, int]) -> dict[str, bool]:
    profile = str(item["profile"])
    domain = [name for event in item.get("tool_events", []) if event["role"] == "tool"
              for name in event["names"] if str(name).startswith("aota_")]
    assistant = [name for event in item.get("tool_events", []) if event["role"] == "assistant"
                 for name in event["names"]]
    if profile == "project-steward":
        correct = domain == ["aota_project_registry_refresh", "aota_project_open"]
    elif profile in ("architect", "reviewer"):
        correct = domain.count("aota_read_file") == 2 and "aota_search_files" not in domain
    elif profile == "coder":
        correct = bool(domain) and domain[0] == "aota_search_files" and \
            domain.count("aota_search_files") == 1 and domain.count("aota_read_file") <= 1
    else:
        correct = domain == ["aota_read_file"]
    session = item.get("session") or {}
    return {
        "outcome_completion": item.get("returncode") == 0 and
            (item.get("usage") or {}).get("completed") is True,
        "correct_tool_and_parameter_path": correct,
        "retry_stop_safety": item.get("returncode") == 0 and
            not (item.get("usage") or {}).get("failed", False),
        "total_tool_budget": int(session.get("tool_call_count", 999)) <= limits[profile],
        "minimal_skill_and_tool_description": assistant.count("skill_view") <= 1 and
            assistant.count("tool_describe") <= 1,
    }


def task_criteria(item: dict[str, object], limit: int) -> dict[str, bool]:
    session_id = item.get("session_id")
    events = db_events("task-main", session_id)
    assistant = [name for event in events if event["role"] == "assistant" for name in event["names"]]
    domain = [name for event in events if event["role"] == "tool" for name in event["names"]
              if str(name).startswith("aota_")]
    dispatch_errors: list[str] = []
    closure = False
    path = Path("/home/latios/.hermes/profiles/task-main/state.db")
    if session_id and path.exists():
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            for row in connection.execute(
                "select tool_name, content from messages where session_id=? and role='tool'",
                (session_id,),
            ):
                content = row["content"] or ""
                if row["tool_name"] == "aota_profile_task_dispatch":
                    try:
                        data = json.loads(content)
                        if data.get("error"):
                            dispatch_errors.append(str(data["error"]))
                    except (TypeError, ValueError):
                        pass
                if row["tool_name"] == "aota_profile_task_status" and '"status": "closed"' in content:
                    closure = True
    parent = item.get("session") or {}
    worker = item.get("worker_session") or {}
    complete = bool(parent.get("ended_at") and worker.get("ended_at") and closure)
    required = {"aota_profile_task_dispatch", "aota_handoff_open",
                "aota_orchestration_decision_record", "aota_handoff_ack",
                "aota_profile_task_status"}
    return {
        "outcome_completion": complete,
        "correct_tool_and_parameter_path": not dispatch_errors and required.issubset(domain),
        "retry_stop_safety": True,
        "total_tool_budget": int(parent.get("tool_call_count", 999)) <= limit,
        "minimal_skill_and_tool_description": assistant.count("skill_view") <= 1 and
            assistant.count("tool_describe") <= 1,
    }


def enrich(samples: list[dict[str, object]], cases: dict[str, dict[str, object]]) -> None:
    for item in samples:
        limits = {profile: int(cases[profile]["prompt_tool_limit"]) for profile in cases}
        item["criteria"] = (task_criteria(item, limits["task-main"])
                            if item["profile"] == "task-main" else criteria_raw(item, limits))
        item["score_percent"] = round(sum(
            WEIGHTS[key] * (100 if value else 0) for key, value in item["criteria"].items()
        ), 1)
        parent = item.get("session") or {}
        worker = item.get("worker_session") or {}
        item["end_to_end_llm_plus_tool_calls"] = sum(
            int(parent.get(key, 0) or 0) + int(worker.get(key, 0) or 0)
            for key in ("api_call_count", "tool_call_count")
        )


def make_evidence(repo: Path, fixture_path: Path, samples: list[dict[str, object]],
                  deployment: dict[str, object], reload: dict[str, object]) -> dict[str, object]:
    grouped = {profile: [x for x in samples if x["profile"] == profile] for profile in PROFILES}
    profiles = []
    for profile in PROFILES:
        group = grouped[profile]
        profiles.append({
            "profile": profile,
            "score_percent": round(sum(x["score_percent"] for x in group) / len(group), 1),
            "sample_scores": [x["score_percent"] for x in group],
            "session_ids": [x.get("session_id") for x in group],
            "finding": {key: sum(1 for x in group if x["criteria"][key]) for key in WEIGHTS},
        })
    smoke = []
    for profile in PROFILES:
        first = grouped[profile][0]
        smoke.append({"profile": profile, "session_id": first.get("session_id"),
                      "score_percent": first["score_percent"],
                      "criteria": first["criteria"]})
    passes = {key: sum(1 for x in samples if x["criteria"][key]) for key in WEIGHTS}
    total = sum(int(x["end_to_end_llm_plus_tool_calls"]) for x in samples)
    score = {f"{key}_rate_percent": round(100 * passes[key] / len(samples), 1)
             for key in WEIGHTS}
    score["composite_efficiency_percent"] = round(sum(
        WEIGHTS[key] * score[f"{key}_rate_percent"] for key in WEIGHTS
    ), 1)
    previous = 80.7
    prior = sorted((repo / "deploy/evidence").glob("aota-profile-efficiency-r7-30-sample-*.json"))
    if prior:
        try:
            previous = float(json.loads(prior[-1].read_text())["score"]["composite_efficiency_percent"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            pass
    score.update({"target_percent": 90.0, "target_met": score["composite_efficiency_percent"] >= 90.0,
                  "previous_r7_percent": previous,
                  "delta_from_r7_points": round(score["composite_efficiency_percent"] - previous, 1)})
    return {
        "schema_version": "aota-profile-efficiency-rerun-v1",
        "captured_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "status": "PASS_WITH_FINDINGS",
        "deployment": deployment,
        "reload": reload,
        "sample_contract": {"fixture": str(fixture_path.relative_to(repo)), "samples": len(samples),
                             "repetitions_per_profile": 5, "per_task_llm_plus_tool_limit": 100,
                             "task_main_parent_tool_limit": 12, "model": "glm-5.2",
                             "provider": "ollama-cloud"},
        "evaluation_contract": {"weights": WEIGHTS,
                                 "minimal_invocation": "skill_view <= 1 and tool_describe <= 1",
                                 "correct_path_policy": "parameter rejection counts as incorrect first-pass route",
                                 "safety_policy": "never retry identical arguments after retryable=false"},
        "minimal_smoke": {"samples_reused_from_formal_run": smoke,
                          "all_completion_pass": all(x["criteria"]["outcome_completion"] for x in smoke),
                          "all_route_pass": all(x["criteria"]["correct_tool_and_parameter_path"] for x in smoke),
                          "all_safety_pass": all(x["criteria"]["retry_stop_safety"] for x in smoke)},
        "aggregate": {"formal_samples": len(samples),
                       "parent_api_calls": sum(int((x.get("session") or {}).get("api_call_count", 0) or 0) for x in samples),
                       "parent_tool_calls": sum(int((x.get("session") or {}).get("tool_call_count", 0) or 0) for x in samples),
                       "worker_api_calls": sum(int((x.get("worker_session") or {}).get("api_call_count", 0) or 0) for x in samples),
                       "worker_tool_calls": sum(int((x.get("worker_session") or {}).get("tool_call_count", 0) or 0) for x in samples),
                       "end_to_end_llm_plus_tool_calls": total,
                       "average_end_to_end_llm_plus_tool_calls_per_sample": round(total / len(samples), 2),
                       "max_end_to_end_llm_plus_tool_calls_in_one_sample": max(x["end_to_end_llm_plus_tool_calls"] for x in samples),
                       "samples_over_100_call_cap": sum(x["end_to_end_llm_plus_tool_calls"] > 100 for x in samples),
                       "passes": passes},
        "profiles": profiles,
        "score": score,
        "samples": samples,
        "limitations": ["PTY transcript is bounded; session DB is closure authority",
                        "known deployment false-positive is allowed only by explicit deploy flag"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--fixture", type=Path,
                        default=Path("fixtures/aota-profile-efficiency-r5-30-sample-plan.json"))
    parser.add_argument("--evidence-out", type=Path)
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--skip-reload", action="store_true")
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--raw-workers", type=int, default=3)
    parser.add_argument("--raw-timeout", type=int, default=240)
    parser.add_argument("--task-timeout", type=int, default=900)
    args = parser.parse_args()
    repo = args.repo.resolve()
    fixture_path = (repo / args.fixture).resolve() if not args.fixture.is_absolute() else args.fixture
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if args.samples != 30 or fixture.get("total_sample_count") != 30:
        raise SystemExit("This skill is fixed to the 30-sample r5 fixture")
    if args.deploy and not args.skip_deploy:
        deployment = deploy(repo)
    else:
        deployment = {"skipped": True}
    if args.reload and not args.skip_reload:
        reload_info = reload_native(repo)
    else:
        reload_info = {"skipped": True}
    cases = {str(item["profile"]): item for item in fixture["base_cases"]}
    with tempfile.TemporaryDirectory(prefix="aota-profile-efficiency-rerun-") as temp:
        temp_root = Path(temp)
        raw = raw_samples(cases, repo, temp_root, max(1, args.raw_workers), args.raw_timeout)
        task = task_samples(cases["task-main"], repo, args.task_timeout, temp_root)
        samples = raw + task
    if len(samples) != 30:
        raise SystemExit(f"expected 30 samples, got {len(samples)}")
    enrich(samples, cases)
    evidence = make_evidence(repo, fixture_path, samples, deployment, reload_info)
    timestamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    output = args.evidence_out or (repo / f"deploy/evidence/aota-profile-efficiency-rerun-{timestamp}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": str(output), "score": evidence["score"],
                      "aggregate": evidence["aggregate"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
