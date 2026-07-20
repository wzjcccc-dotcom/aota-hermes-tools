# AOTA Skill Lifecycle SOP

## Contract and scope

For every Skill record its ID, owner/owning Profile, trigger, scope,
non-goals, canonical source, deployment target, visibility strategy,
activation targets, and behavior smoke. A Skill may be global or profile-local;
its SOUL reference is textual prompt policy, never hard binding.

Global `~/.hermes/skills/<id>/SKILL.md` does not automatically merge into a
named Profile local skill root. Profile-local Skills live below that Profile's
`HERMES_HOME/skills`. Check `skills.disabled`, local scan roots, `external_dirs`,
skill index/`skill_view`, prompt injection, and actual context visibility. Do
not claim activation from a SOUL `Active Skills` list.

## Managed deployment and refresh

The canonical source is `skills/<id>/SKILL.md`; the managed manifest deploys
global Skills to `~/.hermes/skills`. Profile-local copies, when explicitly
chosen later, must be manifest-managed rather than manual copies. This phase
does not create bulk local copies.

`/reload-skills` can rescan part of the catalog but does not guarantee prompt
cache eviction. The conservative completion path after Skill changes is to
recreate every importing process and open a new session. Completion evidence is
source → deployment → visibility → prompt/context → behavior smoke, not merely
`SKILL.md` existence or hash parity.

`AOTA_SKILL_LIFECYCLE_DOC_PASS`
