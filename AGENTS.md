# FreeCadCopilot

## Agent skills

### Issue tracker

Issues live in YouTrack, project `FCC`, via the `yt` CLI — not GitHub Issues. Project config: `myga.yml`.

### Triage labels

Default vocabulary as YouTrack tags — `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`.

### Domain docs

In the YouTrack knowledge base, not in the repo — `FCC-A-3` Project Overview, `FCC-A-1` Glossary, `FCC-A-5` Architecture Decisions (ADR-0001 … ADR-0011).

## YouTrack

Work for this repo lives in YouTrack, not GitHub Issues. Project config: `myga.yml`.
The `myga-*` skills drive it.

`package.xml`'s `<version>` is the version of truth; `Init.py` parses it back, never a literal
of its own. Closing an Epic bumps the minor version to that epic's ticket number and resets
patch to 0 (`0.<epic-number>.0`); closing any other ticket inside that epic bumps patch
(`0.<epic-number>.<patch+1>`).

## Comments

No comments. A measurement, a constraint, a rejected alternative — anything a
reader has to know and cannot read off the code — goes in the docstring of the
function, class or module it belongs to. That is where someone looking for it
goes, and it is what `help()` prints.

Never in a comment, and never anywhere: the story of the change (`used to be`,
`had been`, `now also`), the statistics of the run that produced it, or a
ticket id. The commit message and the ticket hold those, and they are the two
places that stay true.

Machine-readable pragmas — `# type:`, `# noqa:`, a shebang, an encoding line —
are not prose and are exempt.

`tests/test_the_code_says_why_not_what.py` enforces this over `core`, `ui`,
`bridge`, `tools` and `Mod`.

## Module layout

`Mod/` is FreeCAD and Qt: `CopilotPreferences.py` and `Mod/ui/`. `src/` is
the engine and carries no Qt at all, not even a deferred import
(`tests/test_tree_boundary.py`).
`Mod/ui/`'s widgets reach the engine only through `copilot.sdk`, never
`core` directly.

A module that must emit a Qt signal or marshal onto the Qt main thread over a
`core/` concern -- `settings_manager.py`, `provider_manager.py`, `logger.py`,
`main_thread.py` -- goes in `Mod/bridge/` instead, the one place `Mod/ui/`
may import directly, and the only reason a concern ever has two files: one
Qt-free in `src/core/`, one Qt-touching in `Mod/bridge/`, never two of
either.

## Measurements

A number in an Acceptance Criterion is produced by a test, from the sheets in
`tests/modelmania` or the sessions in `logs/`. A measurement that lives only in
a docstring or a commit message is a claim: the next change moves it and
nothing says so.

## Tests

Before adding a test, read the tests that already cover what is being changed.
A test that pins behaviour the change removes is rewritten or deleted in the
same commit as the change, never left standing beside its replacement: what
costs an architecture change is not the test that fails, it is the one that
passes while measuring what the code no longer does.

A test that scans the tree by a hardcoded list of packages, paths or names is
a liability — it must derive that list, or another test must assert the list
is complete. Three guards here held a package tuple written before `copilot/`
existed and stayed green for the whole epic.

## RLM rewrite branch (FCC-291)

This branch, `FCC-291-rlm-rewrite`, is a long-lived fork of `dev`, forked at
the tag `pre-rlm-rewrite-dev-baseline` (commit `030d214`). Every ticket from
FCC-296 onward lands its commits here, not on `dev`. Nothing else lands on
`dev` for the rewrite's whole lifetime — no freeze rule and no reproduce-list
ticket, because the remaining fix work on the current architecture finished
before this epic started (FCC-292/293/294). The rewrite becomes `dev` by
tag-and-replace at cutover (FCC-320), gated on the comparison harness's pass
rate — not by merging this branch into `dev`. See `FCC-291`'s Solution Notes
for the full decision.
