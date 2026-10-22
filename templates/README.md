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
