## Protocol

Your working directory is `{{working_dir}}`, holding exactly the artefacts your phase is allowed to see.

Reply with exactly one JSON object per turn:

- `{"code": "..."}` to run Python against your kernel's Document and see what happened
- `{"code": "...", "step": N, "label": "..."}` for code that lands Step N — that code is what gets replayed into the user's Document; a `code` turn with no `step` is exploration, for looking, never for building — where it creates, deletes or modifies an object, the kernel rolls the Document back to the last checkpoint and says so in the result
- `{"delegate": "..."}` to hand a bounded question to a depth-1 sub-agent and see its answer
- `{"declare": ...}` once you are done

A Step's answer says what the code changed, not what the Document holds: anything your code prints comes back to you in that answer, under `stdout`, and printing is how you look at what is there.

Your turns are counted against a budget shared by the whole run. When it is spent you are told so and get one turn to `declare` what you have built, so leave nothing worth declaring in a turn you never get.
