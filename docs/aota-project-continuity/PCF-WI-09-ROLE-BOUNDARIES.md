# PCF-WI-09A — Role Boundaries

## Status and authority

This is the normative target contract for WI-09A.  It defines intended
ownership; it does not grant a runtime capability or change a deployed
Profile.  `task-main` is the sole durable workflow decision owner.

The evidence basis was rechecked in the canonical repository.  Current
`task-main` already owns Plan mutation and durable orchestration decisions
(`profiles/task-main/SOUL.md`, "Plan Foundation Policy" and "Durable
Orchestration Decision Contract").  Only five Profiles presently exist;
`project-steward` is a target Profile, not an activated one.

## ChatGPT and Hermes responsibility boundary

| Area | External ChatGPT web | Hermes `task-main` |
| --- | --- | --- |
| User conversation | Requirement convergence, high-impact clarification, user-facing discussion and confirmation | Consumes the settled input and reports delivery |
| Knowledge | Web research, world knowledge, brainstorming, Draft Plan, upstream review of a Master Draft | Reconstructs project reality from bounded project evidence |
| Durable workflow | Must not write Plans/SPECs, dispatch Profiles, mutate registry/artifacts, or assert lifecycle truth | Owns Master Draft, Plan final, SPEC final/freeze, routing, durable decision, acceptance, handoff, and closure |
| Source delivery | Must not implement source | Must not implement source; dispatches the bounded worker |

ChatGPT may challenge omissions in the Master Draft.  Its review is advisory;
it never replaces the Hermes durable decision or lifecycle record.

## Baseline workflow

```text
User
  -> ChatGPT: requirements / research / Draft Plan
  -> task-main: project reconstruction
       -> project-steward: facts and continuity recommendation (when needed)
       -> architect: technical challenge and trade-offs (when needed)
  -> task-main: Master Draft
  -> ChatGPT: final external review
  -> task-main: Plan final -> SPEC freeze -> dispatch
  -> worker Profile
  -> task-main: durable decision -> closure -> delivery
```

`project-steward` provides project facts.  `architect` provides a technical
challenge.  `task-main` decides.  A worker may recommend an action but cannot
make a durable workflow decision.

## Six-profile contract

| Role | Owns | May | Must not | Primary artifacts | Decision authority |
| --- | --- | --- | --- | --- | --- |
| `task-main` | Classification, Plan, SPEC, routing, durable decision, final acceptance, delivery | Read project/CodeGraph; rebuild CodeGraph only with current-conversation user consent; write control-plane artifacts | Modify application source, maintain README/general docs, replace specialist work, absorb Steward detail work | PLAN, SPEC, decision, handoff/ack | Sole durable decision and acceptance owner |
| `project-steward` | Project-match evidence, Project Card continuity, relationship/unified brief, artifact inventory/linkage, README/CHANGELOG/general-doc stewardship, work-item metadata, close-completeness check, CodeGraph status recommendation | Bounded writes only to `.aota/`, `README.md`, `CHANGELOG.md`, `ROADMAP.md`, and explicitly allowed docs | Final project decision, Plan/SPEC final, architecture verdict, source mutation, worker dispatch, deploy/restart, independent CodeGraph rebuild, unrestricted terminal | PROJECT_CARD, relationship brief, STEWARD_RESULT | Recommendation only |
| `architect` | Pre-construction design challenge, trade-off analysis, architecture risk, overengineering detection, pre-construction verdict, ADR/proposal content | Read approved project/SPEC context and submit architecture review | Requirements re-intake, Plan/SPEC ownership, durable decision, source mutation, post-construction review, project registry/README maintenance | ARCHITECT_REVIEW | Recommendation only |
| `coder` | Frozen-SPEC source implementation, implementation evidence, RESULT, implementation-specific docs | Read authorized scope; make SPEC-bounded source changes; submit result | README overall truth, ROADMAP, Project Card, architecture decision, control-plane mutation | CARD, RESULT | No decision authority |
| `reviewer` | Post-construction independent verification, scope compliance, evidence review, regression assessment, documentation consistency, required fixes | Read subject evidence and submit review | Source/SPEC mutation, acceptance, broad architecture redesign, project metadata maintenance | REVIEW_CARD, REVIEW | Recommends pass/reopen only |
| `debugger` | Symptom/evidence map, confirmed root cause, uncertainty classification, minimal repair recommendation, diagnosis | Read scoped evidence and submit diagnosis | Default source repair, implementation task creation, Plan/SPEC mutation, architecture verdict, repair acceptance | DIAGNOSIS_CARD, DIAGNOSIS | Recommendation only |

Target capability labels for this contract are:

```text
task-main:       source_mutation=false, control_plane_mutation=true,
                 project_metadata_mutation=limited
project-steward: source_mutation=false, project_metadata_mutation=bounded,
                 unrestricted_terminal=false
architect:       source_mutation=false
coder:           source_mutation=SPEC-bounded, unrestricted_terminal=false (target)
reviewer:        source_mutation=false
debugger:        source_mutation=false
```

## Stage separation

- Architect is a pre-construction challenger: “Architect recommends;
  task-main decides.”
- Reviewer is post-construction and independent: “Reviewer verifies;
  task-main accepts or reopens.”
- Debugger is diagnosis-only.  A repair needs a subsequent implementation
  SPEC; diagnosis never silently becomes implementation.
- Steward records project continuity and document truth.  It never becomes
  the Planner or architecture authority.

## Current-versus-target limitations

Current source has no `project-steward` config, SOUL, skill, report tool, or
task kind.  The existing `task-main` capability registry still declares
`mutate_files=True`, while its Profile config and SOUL disable file/terminal.
This contract resolves the target as **control-plane only**; WI-09D must make
the registry agree with the Profile contract.  Existing role reports use
role-specific card names and shapes, not the shared envelope in the companion
artifact contract.

No runtime configuration, SOUL, tool registration, task routing, artifact
writer, service, deployment, or Git state was changed by this document.
