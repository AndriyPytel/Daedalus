## Editing

The Document already holds the Assembly, and the intent asks for a change to it, not for a new one. Look at its parts, joints and grounded part first, then change only what the intent names: every other part and joint keeps its label, shape, type and frames, and the Assembly stays the one you were given.

- Remove: delete the named part and every joint that names it, and nothing else.
- Add: import the part file the intent names as a `Part::Feature` labelled for it, put it in the Assembly, and join it with the joint, type and connector frames the intent gives.
- Change a joint: edit that joint in place, with the type the intent gives and the frames it already has.

`finish` is judged on the whole Assembly, not on the change: it must still meet everything above.
