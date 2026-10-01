# The ladder — model generation prompts

The rungs of DAE-29: real published parts, run by hand against a live provider,
each judged by a number its source publishes. The prompts and their targets
belong here, one section per rung.

**The rungs themselves are not written down yet.** DAE-30 (rung 1, a CSWA
bracket) was run and closed with this file's absence recorded as a waived
criterion; rung 2 needs it. Filling it in is DAE-29's work and DAE-31's
blocker, not this file's creation.

## How a rung is run, and what may not close one

A rung is run **by hand, in the GUI**, against a live provider. That is the
whole point of the ladder: it measures the product a user gets.

**No viewport criterion may be closed through `tools/run.py`.**
`run.py` is headless, so it keeps the Preflight Worker's rasteriser
fallback — one isometry, not the four viewport views Review is given in the
GUI (ADR-0015). The only scriptable live lane therefore stops reproducing the
product at exactly the point the picture is made. A rung that passes under
`run.py` has not been shown to pass for a user.
