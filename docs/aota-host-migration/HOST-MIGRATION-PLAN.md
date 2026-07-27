# AOTA Forge Host Migration Plan

Fixed order:

1. HOST-WI-00 — Docker Baseline Capture and Offline Rollback Preservation
2. HOST-WI-01 — Vanilla Hermes Host Installation and Desktop Remote Connection
3. HOST-WI-02 — AOTA Forge Host Runtime Projection
4. HOST-WI-03 — Named Profile and task-main Host Activation
5. HOST-WI-04 — Profile Task Host Execution Closure
6. HOST-WI-05 — Durable task-main Completion Wakeup Restoration
7. HOST-WI-06 — Delegate Runtime, AMF and CodeGraph Capability Migration
8. HOST-WI-07 — Legacy WebUI and Docker Runtime Retirement Closure

HOST-WI-00 is a checkpoint. It must leave Docker as `OFFLINE_STANDBY` only when all gates pass, while preserving containers, images, volumes, networks, Compose configuration, `.env`, `.hermes`, and overrides. It does not install Host Hermes, start `hermes serve`, test HOST-WI-01, or change Profile, Skill, Plugin, Agent, or WebUI behavior.
