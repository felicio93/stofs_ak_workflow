# Templates (one folder per model, mirroring the M{ID}/ project tree)

`templates/<model>/config/` is a complete, ready-to-copy config set for one `model_type`, taken
from the four validated Hercules reference runs and corrected for the current
code:

| Folder | model_type | Reference run | Files |
|--------|------------|---------------|-------|
| `schism/config/` | `schism` | M11 | project, domain, steps, envs, `schism.yaml`, postprocess, build |
| `ufs_schism/config/` | `ufs_schism` | M12 | project, domain, steps, envs, `ufs_schism.yaml`, postprocess, build |
| `schism_wwm/config/` | `schism_wwm` | M13 | project, domain, steps, envs, `schism_wwm.yaml`, postprocess, build |
| `ufs_schism_ww3/config/` | `ufs_schism_ww3` | M14 | project, domain, steps, envs, `ufs_schism_ww3.yaml`, postprocess, build |

```bash
export CFG=<project_dir>/M11/config            # M12/M13/M14 for the other models
mkdir -p $CFG
cp $WF/templates/schism/config/*.yaml $CFG/
stofs-ak --init --config $CFG
```

Notes

- Values are those of the reference runs: Hercules paths and conda base,
  `project_id` 11 / 12 / 13 / 14, short test period `2025-09-01` .. `2025-09-08`
  with `grouping: ndays`, `group_ndays: 2`, and **every applicable step set to
  `true`**. Edit `project_id`, `project_dir`, dates, grouping and `steps.yaml`
  for a new project (enable steps one at a time when testing).
- Only the active `<model_type>.yaml` is loaded, so each model yaml is
  self-contained (it repeats the SCHISM keys). Do not add a separate
  `schism.yaml` for the other models; it would be ignored.
- Steps that do not apply to a model are not listed. In-run diagnostic plots
  (`diag_run_plots`) exist only for `schism`.
- `combine_output*` settings appear only in the UFS models (the only ones that
  combine old-I/O output stacks).
- `chain_ww3_restart` (UFS+WW3) is `true`: WW3, DATM and mediator restarts are
  chained between groups.
- Run `python utils/check_config_templates.py` after editing a template.

## fix/ starter files

`templates/<model>/fix/` holds the files of `M{ID}/fix/` that are not specific
to a mesh. `stofs-ak --new-project` copies them, and `stofs-ak --check-fix`
reports which ones you edited. They are taken from the validated reference
runs; `ufs_schism_ww3` only stores what differs from `ufs_schism` (the rest is
looked up there).

| File | Origin | Notes |
|------|--------|-------|
| `param.nml` | M11 (`schism`), M13 (`schism_wwm`), M12 (`ufs_schism*`; M14 is identical) | `ufs_*` differ from M11 in `nws = 4` (required by USE_ATMOS), `ihot = 1` (M11 has `ihot = 2`; both intentional) and fewer old-I/O outputs; M13 adds `icou_elfe_wwm = 1`, `nstep_wwm = 10` and the `iof_wwm` block. The workflow rewrites `start_*`, `rnday`, `nhot_write` and `ihot` per group (`gen_param`). |
| `run_test`, `run_comb` | M11 to M14 | Job cards. `-J` and `-D` (and the WW3 `srun` executable, and the `combine_hotstart7 -i` step) are rewritten per run. `-N/-n` must match the partitioning (and the `*_petlist_bounds`). `@@MODULES:<kind>@@` is replaced by the `module load` lines of the build toolchain, so jobs run with the modules the executable was built with (the reference jobs sourced a personal `~/modules_schism.hercules`). `run_comb` uses account `nosofs` (intentional; `run_test` uses `nos-surge`). |
| `wwminput.nml` | M13 | `gen_wwminput` sets dates, hotstart flag and stations. |
| `datm_in`, `datm.streams`, `model_configure` | M12 (M14 identical) | Workflow placeholder style (`@[NAME]`); filled per group. |
| `fd_ufs.yaml`, `noahmptable.tbl` | **UFS-Coastal `tests/parm/` at d84244a3** (`feature/coastal_app`, v1.0.0), copied byte for byte | The M12/M14 `fd_ufs.yaml` was an older, longer file (it lacked the newer `Sa_vfrac`/`Sa_zorl` entries and had FV3/JEDI sections). The field dictionary should match the UFS version that is built, so the upstream file is used. `noahmptable.tbl` differs from the M12 copy only in trailing whitespace. |
| `ww3_shel.nml`, `ww3_ounp.inp`, `namelists.nml` | M14 | `gen_ww3_shel` / `gen_ww3_ounp` set dates and output counts. |

Mesh-specific files (`hgrid.*`, `vgrid.in`, `partition.prop`, `*.gr3`,
`station.in`, `source_*.csv`, and the WW3 mesh files) are not templated;
`--check-fix` lists them.
