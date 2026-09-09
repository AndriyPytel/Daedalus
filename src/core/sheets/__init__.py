"""core.sheets package: the drawing is read here and the sketch checked against
it; sketch_check imports sheet_reader and nothing else in core imports either
at module load."""
from . import sheet_reader, sketch_check  # noqa: F401
