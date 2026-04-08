# PITFALLS: FreeCAD LLM Addon Extensions

## 1. TopoNaming Hallucinations
- **Warning Sign**: LLM tries to map `Face1` to a sketch when the underlying body has been recomputed.
- **Prevention**: In-agent scripting validation where it asks the geometry layer what faces exist before generating coordinate mappings.

## 2. Segfaulting on Exceptions
- **Warning Sign**: Execution runtime breaks because `Transaction` isn't fully aborted when Python `exec()` fails natively inside FreeCAD bindings.
- **Prevention**: Robust nested exception handling inside `command_executor.py`; guaranteeing `doc.abortTransaction()` executes even if Python segfaults.

## 3. Asymmetric State
- **Warning Sign**: Agent thinks the feature was added but FreeCAD `recompute()` actually pruned it.
- **Prevention**: Final validation check where the addon explicitly reads the tree diff.
