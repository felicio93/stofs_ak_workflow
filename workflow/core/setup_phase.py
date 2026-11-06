"""
core/setup_phase.py
===================
--phase setup: the steps that build fix/ from the mesh, once per project.

They depend on hgrid.gr3 (and modelsetup.yaml) but not on the simulation
period, so they run before the per-group preprocessing. The order is fixed
because each step reads what the earlier ones made:

  gen_vgrid         fix/vgrid.in from hgrid.gr3 and the vgrid: block
  gen_partition     fix/partition.prop (metis_prep reads vgrid.in)
  gen_constant_gr3  constant property files
  replicate_fix     property files interpolated from replicate_from
  gen_nudge_gr3     TEM/SAL_nudge.gr3 along the open boundaries
  gen_estuary       fix/estuary.gr3 + bin/*.in for the HYCOM utilities
  gen_wwmbnd        fix/wwmbnd.gr3 (schism_wwm)
  gen_ww3_mesh      mesh.msh, mod_def.ww3, ww3_ESMFmesh.nc (ufs_schism_ww3)
  gen_ww3_points    ww3_points.list from station.in (ufs_schism_ww3)
  inspect_mesh      plots of every fix/ file (SLURM job)

A driver lists the steps it supports in `setup_steps`; an enabled step it
does not support is reported as [N/A] with the driver's reason (na_steps)
or "not part of model_type ...".
"""

from typing import Optional

SETUP_STEPS = (
    "gen_vgrid", "gen_partition", "gen_constant_gr3", "replicate_fix",
    "gen_nudge_gr3", "gen_estuary", "gen_wwmbnd", "gen_ww3_mesh", "gen_ww3_points",
    "inspect_mesh",
)


def _gen_vgrid(cfg, config_dir):
    from workflow.core.vgrid import run_gen_vgrid
    run_gen_vgrid(cfg)


def _gen_partition(cfg, config_dir):
    from workflow.models.schism.preprocess.gen_partition import (
        run_gen_partition,
    )
    run_gen_partition(cfg)


def _gen_constant_gr3(cfg, config_dir):
    from workflow.models.schism_2d.preprocess.gen_constant_gr3 import (
        run_gen_constant_gr3,
    )
    run_gen_constant_gr3(cfg)


def _replicate_fix(cfg, config_dir):
    from workflow.models.schism.preprocess.replicate_fix import (
        run_replicate_fix,
    )
    run_replicate_fix(cfg)


def _gen_nudge_gr3(cfg, config_dir):
    from workflow.models.schism.preprocess.gen_nudge_gr3 import (
        run_gen_nudge_gr3,
    )
    run_gen_nudge_gr3(cfg)


def _gen_estuary(cfg, config_dir):
    from workflow.models.schism.preprocess.gen_estuary import (
        run_gen_estuary,
    )
    run_gen_estuary(cfg)


def _gen_wwmbnd(cfg, config_dir):
    from workflow.models.schism_wwm.preprocess.gen_wwmbnd import (
        run_gen_wwmbnd,
    )
    run_gen_wwmbnd(cfg)


def _gen_ww3_mesh(cfg, config_dir):
    from workflow.models.ufs_schism_ww3.preprocess.gen_ww3_mesh import (
        run_gen_ww3_mesh,
    )
    run_gen_ww3_mesh(cfg, config_dir)


def _gen_ww3_points(cfg, config_dir):
    from workflow.models.ufs_schism_ww3.preprocess.gen_ww3_points import (
        run_gen_ww3_points,
    )
    run_gen_ww3_points(cfg)


def _inspect_mesh(cfg, config_dir):
    from workflow.diagnostics.submit_inspect_mesh import submit_inspect_mesh
    return submit_inspect_mesh(cfg, config_dir)


_RUNNERS = {
    "gen_vgrid": _gen_vgrid,
    "gen_partition": _gen_partition,
    "gen_constant_gr3": _gen_constant_gr3,
    "replicate_fix": _replicate_fix,
    "gen_nudge_gr3": _gen_nudge_gr3,
    "gen_estuary": _gen_estuary,
    "gen_wwmbnd": _gen_wwmbnd,
    "gen_ww3_mesh": _gen_ww3_mesh,
    "gen_ww3_points": _gen_ww3_points,
    "inspect_mesh": _inspect_mesh,
}


def run_setup(driver, only: Optional[str] = None) -> list:
    """Run the enabled setup steps of a driver in order; return the SLURM
    job ids submitted (inspect_mesh)."""
    cfg = driver.cfg
    model_type = str(cfg.get("model_type", "schism")).lower()
    na = getattr(driver, "na_steps", {}) or {}
    jobs = []
    for step in SETUP_STEPS:
        wanted = (step == only) if only is not None else bool(
            cfg.get(step, False))
        if step not in driver.setup_steps or step in na:
            if wanted:
                reason = na.get(step, f"not part of model_type={model_type}")
                print(f"[N/A]  {step}  ({reason})")
            continue
        if not wanted:
            print(f"[SKIP] {step}")
            continue
        print(f"[STEP] {step}")
        jid = _RUNNERS[step](cfg, driver.config_dir)
        if jid:
            jobs.append(jid)
    return jobs
