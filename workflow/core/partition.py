"""
core/partition.py
=================
partition.prop helpers shared by the gen_partition step and --check-fix.

partition.prop (offline partition, NO_PARMETIS builds) has one line per
element of hgrid.gr3, "<element id> <rank>", with ranks 0..ncompute-1, where
ncompute is the number of compute cores: the job card's -n minus the scribe
cores (the last argument of the srun line).

  compute_cores(run_test)          -> (ntasks, nscribes, ncompute) or None
  check_partition(fix, ncompute)   -> list of error strings (empty when valid)
"""

import re
from pathlib import Path

_NTASKS_RE = re.compile(r"(?m)^#SBATCH\s+(?:-n\s*|--ntasks[=\s]+)(\d+)")
_SRUN_RE   = re.compile(r"(?m)^\s*srun\b[^\n]*?\./\S+\s+(\d+)\s*$")


def compute_cores(run_test: Path):
    """(ntasks, nscribes, ncompute) of a SCHISM job card, or None when the
    #SBATCH -n line or the scribe argument of the srun line is missing."""
    text = Path(run_test).read_text()
    n = _NTASKS_RE.search(text)
    s = _SRUN_RE.search(text)
    if not (n and s):
        return None
    ntasks, nscribes = int(n.group(1)), int(s.group(1))
    return ntasks, nscribes, ntasks - nscribes


def mesh_ne(hgrid: Path) -> int:
    """Element count from the second line of hgrid.gr3."""
    with open(hgrid) as f:
        f.readline()
        return int(f.readline().split()[0])


def check_partition(fix: Path, ncompute: int = None) -> list:
    """Check fix/partition.prop against fix/hgrid.gr3 (one line per element,
    ids 1..ne in order) and, when given, the compute core count."""
    fix  = Path(fix)
    prop = fix / "partition.prop"
    if not prop.exists() or not (fix / "hgrid.gr3").exists():
        return []
    import numpy as np
    ne = mesh_ne(fix / "hgrid.gr3")
    try:
        data = np.loadtxt(prop, dtype=np.int64, ndmin=2)
    except ValueError as exc:
        return [f"partition.prop: unreadable ({exc})"]
    errors = []
    if data.shape[1] < 2:
        return ["partition.prop: expected two columns (element id, rank)"]
    if len(data) != ne:
        errors.append(f"partition.prop has {len(data)} lines but hgrid.gr3 "
                      f"has {ne} elements (made for another mesh?)")
    elif not np.array_equal(data[:, 0], np.arange(1, ne + 1)):
        errors.append("partition.prop: element ids are not 1..ne in order")
    ranks = np.unique(data[:, 1])
    if ncompute is not None and (len(ranks) != ncompute
                                 or ranks[0] != 0
                                 or ranks[-1] != ncompute - 1):
        errors.append(f"partition.prop has ranks {ranks[0]}..{ranks[-1]} "
                      f"({len(ranks)} parts) but run_test has {ncompute} "
                      f"compute cores (-n minus scribes)")
    return errors
