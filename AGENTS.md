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
`utils`, `tools` and `Mod`.

## Measurements

A number in an Acceptance Criterion is produced by a test, from the sheets in
`tests/modelmania` or the sessions in `logs/`. A measurement that lives only in
a docstring or a commit message is a claim: the next change moves it and
nothing says so.

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
