# Registry smoke fixture

Registry cases are created in a temporary workspace by `scripts/verify-project-continuity.py` so canonical source remains free of generated `.aota/registry/projects.json` artifacts. Covered cases include first refresh, unchanged refresh, manifest change, malformed rebuild, and duplicate IDs.
