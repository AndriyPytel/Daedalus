## Protocol

Your working directory is `{{working_dir}}`, holding exactly the artefacts your phase is allowed to see.

Reply with exactly one JSON object per turn:

- `{"code": "..."}` to run Python against your kernel's Document and see what happened
- `{"delegate": "..."}` to hand a bounded question to a depth-1 sub-agent and see its answer
- `{"declare": ...}` once you are done
