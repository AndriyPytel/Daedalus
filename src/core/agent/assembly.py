"""
The Assembly Domain: the agent writes FreeCAD Python against the native Assembly workbench, and `finish` is refused until the Document holds one Assembly whose parts all reach a grounded part through joints, whose `solve()` returns 0 and leaves every Fixed, Revolute and Ball joint met, and no two parts sharing more than a thousandth of the smaller one.
The solver returns 0 for some inconsistent joints and leaves one unsatisfied, so a Fixed, Revolute or Ball joint whose frames do not meet after solving counts as over-constrained.
"""
import json
import os

from core.agent.domain import ASSEMBLY, Domain
from core.agent.solo import run_solo
from core.loop.prompt import _as_image
from core.prompts import read_prompt

MAX_TURNS = 60
INTERFERENCE_SHARE = 0.001
STEP_SUFFIXES = (".step", ".stp")
SOLVE_FAULTS = {-1: "the solver failed", -2: "redundant joints", -3: "conflicting joints",
                -4: "the joints are over-constrained", -5: "a joint is malformed",
                -6: "no grounded part"}

FACTS_CHECK = """
import itertools
import json

import Part

MEETING = ("Fixed", "Revolute", "Ball")
assemblies = [o for o in doc.Objects if o.TypeId == "Assembly::AssemblyObject"]
facts = {"assemblies": len(assemblies)}
if len(assemblies) == 1:
    assembly = assemblies[0]
    doc.recompute()

    def solid(o):
        return getattr(o, "Shape", None) is not None and not o.Shape.isNull() and o.Shape.Solids

    parts = [o for o in assembly.Group if o.TypeId != "Assembly::JointGroup" and solid(o)]
    held = {o.Name for o in parts}
    grounded = [o.ObjectToGround for o in doc.Objects
                if hasattr(o, "ObjectToGround") and o.ObjectToGround is not None
                and o.ObjectToGround.Name in held]
    facts.update(
        parts=[o.Label for o in parts],
        loose=[o.Label for o in doc.Objects
               if o is not assembly and not o.InList and solid(o) and o.Name not in held],
        grounded=sorted({o.Label for o in grounded}),
        joints=len([o for o in doc.Objects if hasattr(o, "JointType") and hasattr(o, "Reference1")]),
        solve=assembly.solve())
    facts["links"] = [[o.Reference1[0].Label, o.Reference2[0].Label] for o in doc.Objects
                      if hasattr(o, "JointType") and hasattr(o, "Reference1")]
    facts["unsatisfied"] = []
    for joint in doc.Objects:
        if getattr(joint, "JointType", None) in MEETING and hasattr(joint, "Reference1"):
            one, other = joint.Reference1[0], joint.Reference2[0]
            frame, there = one.Placement * joint.Placement1, other.Placement * joint.Placement2
            gap = (frame.Base - there.Base).Length
            turned = joint.JointType == "Fixed" and not frame.Rotation.isSame(there.Rotation, 1e-6)
            if gap > 1e-3 or turned:
                facts["unsatisfied"].append({"joint": joint.Name, "type": joint.JointType,
                                             "a": one.Label, "b": other.Label, "gap": gap,
                                             "turned": turned})
    facts["pairs"] = []
    for one, other in itertools.combinations(parts, 2):
        if one.Shape.BoundBox.intersect(other.Shape.BoundBox):
            common = max(0.0, one.Shape.common(other.Shape).Volume)
            if common > 0:
                facts["pairs"].append({"a": one.Label, "b": other.Label, "common": common,
                                       "smaller": min(one.Shape.Volume, other.Shape.Volume)})
print("FACTS " + json.dumps(facts))
"""


def measured(kernel):
    """The Assembly's facts as the kernel's solver leaves them, or `None` where the measuring itself failed; the Document is rolled back."""
    result = kernel.check(FACTS_CHECK, exploring=True)
    for line in reversed((result.stdout or "").splitlines()):
        if line.startswith("FACTS "):
            return json.loads(line[len("FACTS "):])
    return None


def refusals(facts):
    """What fails of the Assembly Assertion, with the numbers, in the order the agent can mend them; empty where nothing fails."""
    count = facts["assemblies"]
    if count != 1:
        return ["The Document holds %s Assembly::AssemblyObject; it must hold exactly one. Create it "
                "with doc.addObject('Assembly::AssemblyObject', 'Assembly') and set Type to 'Assembly'."
                % ("no" if count == 0 else count)]
    found = []
    if facts["loose"]:
        found.append("Parts not in the Assembly: %s. Add each with assembly.addObject(part)."
                     % ", ".join(facts["loose"]))
    if not facts["joints"]:
        found.append("The Assembly has no joint. Join the parts with JointObject joints.")
    if not facts["grounded"]:
        found.append("The Assembly has no grounded part. Ground one with JointObject.GroundedJoint.")
    reached = set(facts["grounded"])
    spread = True
    while spread:
        spread = False
        for one, other in facts["links"]:
            if (one in reached) != (other in reached):
                reached |= {one, other}
                spread = True
    stranded = [label for label in facts["parts"] if label not in reached]
    if facts["grounded"] and stranded:
        found.append("Parts not joined to the grounded part by any chain of joints: %s. Join each to "
                     "a part that is." % ", ".join(stranded))
    solve = facts["solve"]
    if solve != 0 and not (solve == -6 and not facts["grounded"]):
        found.append("assembly.solve() returned %d: %s." % (solve, SOLVE_FAULTS.get(solve, "unknown")))
    for joint in facts["unsatisfied"]:
        found.append("The joints are over-constrained: %s joint %s between %s and %s is not met after "
                     "solving, its frames are %.4g mm apart%s. Two joints ask for different things; "
                     "remove or change one." % (joint["type"], joint["joint"], joint["a"], joint["b"],
                                                joint["gap"], " and turned apart" if joint["turned"] else ""))
    for pair in facts["pairs"]:
        if pair["common"] > pair["smaller"] * INTERFERENCE_SHARE:
            found.append("Parts %s and %s interfere: they share %.4g mm3, %.3f%% of the smaller part "
                         "(at most %.1f%% is allowed)." % (pair["a"], pair["b"], pair["common"],
                                                          100 * pair["common"] / pair["smaller"],
                                                          100 * INTERFERENCE_SHARE))
    return found


def unfinished(kernel):
    """Why the Document is not a finished Assembly, one line per failing condition, or `None` where the Assertion holds."""
    facts = measured(kernel)
    if facts is None:
        return "The Assembly could not be measured: the Document would not recompute or solve."
    found = refusals(facts)
    return ("The Assembly Assertion does not hold:\n- " + "\n- ".join(found)
            + "\nFix this, then finish.") if found else None


def opening(intent, attachments):
    """The first message: the intent, the STEP files named by path, and any drawing as a picture."""
    steps = [path for path in attachments if path.lower().endswith(STEP_SUFFIXES)]
    blocks = [{"type": "text", "text": intent}]
    if steps:
        blocks.append({"type": "text", "text": "The part files:\n" + "\n".join(
            "- %s (%s)" % (path, os.path.splitext(os.path.basename(path))[0]) for path in steps)})
    blocks += [block for block in map(_as_image, (p for p in attachments if p not in steps)) if block]
    return blocks


def run_assembly(intent, *, llm, kernel, tools, events, attachments=(), cancelled=None):
    """Build the Assembly *intent* asks for from the parts in the Document and *attachments*, under the Assembly Domain."""
    domain = Domain(ASSEMBLY, read_prompt("assembly.md"), opening(intent, list(attachments)),
                    lambda: None, unfinished)
    return run_solo(intent, domain=domain, llm=llm, kernel=kernel, tools=tools, events=events,
                    attachments=attachments, cancelled=cancelled, max_turns=MAX_TURNS)
