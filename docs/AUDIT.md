# Audit record — stofs_ak_workflow

Static review of the whole package (core, downloaders, diagnostics, tidal,
orchestrator, SCHISM / SCHISM+WWM / UFS-SCHISM / UFS-SCHISM+WW3 models).
It was done without access to Hercules, so nothing below was executed on the
HPC. Every item was reviewed one by one; the outcome of each decision is
recorded here.

Legend: **Fixed** = code changed. **Documented** = behaviour kept, now
described. **Reported** = known limitation, deliberately not changed.

Paths are relative to the repo root; `models/` = `workflow/models/`.

## 1. Fixed

### Run management (`models/schism/templates/auto_hotstart.py`, `setup_run.py`)

| ID | Change |
|----|--------|
| A1 | A non-zero job exit code without a SCHISM/MPI error signature is no longer reported as a fatal diagnosis, so the intended "resubmit up to 5 times" path is reachable (node failure, OOM, preemption). |
| A2 | `squeue` failures are retried and reported as "queue state unknown"; an unknown state never triggers a resubmit (it previously looked like "job finished"). The user lookup falls back to `getpass`. |
| A7 | `_clean_outputs()` also removes a stale `outputs/fatal.error`. |
| U5 | `chain_ww3_restart` is now honoured. The auto_hotstart template has two new keys: `CHAIN_UFS_RESTARTS` (DATM + mediator restarts; UFS models) and `CHAIN_WW3_RESTART` (WW3 restart). Standalone SCHISM / SCHISM+WWM no longer try UFS restarts (no spurious warnings). Missing key defaults to `true`. **The template default changed from `false` to `true`; a project config copied from the old template must be set to `true` to keep WW3 restart chaining.** |
| – | `setup_run` aborts if the rendered `auto_hotstart.py` has any unreplaced `{{KEY}}`; `submit_run` uses the same generic guard instead of checking two keys. |

### Orchestrator and config

| ID | Change |
|----|--------|
| O1 | `POSTPROCESS_STEPS` complete (`wave_skill`, altimetry steps, `plot_outputs_ww3`); `--phase run` warning now lists only real preprocess steps. |
| O2 | `--refresh` also clears `gen_wwminput`, `gen_ww3_shel`, `gen_ww3_ounp`, `wave_skill`, `plot_outputs_ww3` sentinels, honours `datm_subdir`, and no longer lists never-written entries. It intentionally still does **not** clear `combine_output.done` or `convert_ww3_sta.done` (re-running those after partitions were removed is not safe). |
| O3 | `--only <step>` is validated against the steps of the chosen phase and fails with a hint naming the correct phase. |
| B1 | Warning (not error) when `grouping: monthly` is used with a `start_date` that is not the 1st of a month. |
| N1 | Warning when `grouping: weekly/daily/biweekly` and an explicit different `group_ndays` are both set. |
| K1 | Warning when a nested block (`slurm:`, `executables:`, …) is defined in two config files, and a note when the `<model_type>.yaml` file is missing. |
| K2 | `wait_for_slurm_jobs`: a failed `squeue` is retried, and final job states are checked with `sacct`; a failed/cancelled job stops `--phase all` before the run phase. |
| K3 | `SlurmSubmitter.render` warns about unreplaced `{{KEY}}` placeholders. |
| D1 | `run_download*` return a status; the SCHISM and UFS drivers stop with a clear error when a download had failures instead of continuing into aggregation. |

### Drivers

| ID | Change |
|----|--------|
| C1 | `inspect_mesh.sbatch` `{{MAILUSER}}` is now substituted. |
| C5 | `gen_wwmbnd` added to the WWM `steps.yaml` template (now under `templates/config_example/schism_wwm/`). |
| C8 | The false "[N/A] … model_type=schism" message under `schism_wwm` is gone (driver attribute `supports_wwm_steps`); N/A messages print the actual `model_type`. |
| C9 | `schism_mice.run()` / `postprocess()` raise `NotImplementedError` like `preprocess()` (previously ran plain SCHISM). |
| – | UFS-based models: in-run diagnostic plots removed entirely (`diag_run_oldio.py`, `run_diag_oldio.sbatch`, the diag rendering in both UFS `setup_run.py`, `combine_diag_*` keys). `diag_run_plots` on a UFS model prints `[N/A]`. (`schism_wwm` already rendered diagnostics as disabled; it now prints the same `[N/A]` instead of silently ignoring the flag.) |

### Data / physics-adjacent

| ID | Change |
|----|--------|
| B2 | WWM `BEGTC`/`ENDTC` derive from the group's `param.nml` (`start_*`, `rnday`) so WWM and SCHISM share a time window. Identical output for a group starting 00:00 on day 1. |
| B4 | `aggregate_hycom` interpolates days missing **inside** a group (≤ 3 consecutive, good neighbours within 7 days on both sides; marked `repaired_by_workflow`); a longer gap fails that stack instead of silently building a short one. Missing read-ahead pad days stay a warning. |
| D2 (part) | `_interp_nc` (corrupt-day repair and gap fill) now writes the filled day's `time` value instead of the previous day's. |
| D3 | ERA5 zip merge writes to `*.part` and moves into place when complete. |
| E2 | `layer: bottom` returns the first valid level (levels are stored bottom→surface); it previously returned the surface. |
| E6/E7 | `station_skill` selects the model column by station.in line index (correct with several depths per station) and now assesses salinity (`S`/`SAL`/`SALINITY`, CO-OPS `salinity` product, `staout_6`). |
| E8/E9 | NDBC: missing-value sentinels are applied per column (999 hPa pressure is no longer dropped); 2-digit years map 00–49→2000s, 50–99→1900s. |
| E11 | Wave direction skill uses circular mean, wrapped differences and circular correlation; per-column NDBC sentinels in `wave_skill`. |
| E20 | Serial Argo collocation window includes the last day (same as the daily path). |
| V1 | `plot_sflux` frame labels carry the real hour (00/06/12/18Z) and the duplicate 24Z frame is dropped. |
| V2 | `inspect_mesh` docs/printed names say `.jpg` (the files always were JPEG). |

### Packaging and hygiene

- `pyproject.toml` ships `schism_wwm` and `ufs_schism_ww3` templates; `models/ufs_schism/run/__init__.py` added.
- Removed unreferenced files: root-level copies `schism_wwm/templates/collocate_altimetry_*.sbatch`, `ufs_schism/templates/run_combine_output.sbatch`; never-rendered `compare_sst_mpi`, `plot_outputs_frames`, `plot_outputs_ww3_mpi`; unreachable `plot_hs.py`.
- Removed dead config keys from templates (`datm_fill_value`, `datm_time_units_template`, `run_type`, `restart_n`, `compare_sst_max_ntasks`, argo `*_hist_color`), dead substitutions and helpers, 36 unused imports, `ProgressTracker.label`, `datetime.utcnow()`.
- Config templates: the shared `templates/config_example/*.yaml` and the per-model yamls under `workflow/models/*/templates/config/` were replaced by one complete, checked folder per model (`templates/config_example/<model>/`, from the four validated reference runs) plus `utils/check_config_templates.py`.
- Conventions: `Optional[str]` for `only`; shared `DEFAULT_ACCOUNT/PARTITION/MAIL_USER` in `core/slurm.py`; shared `is_dtn()/dtn_skip()` in `core/environment.py`; `core/templating.py`; `--group` accepted as alias of `--month` in the plot/diag CLIs; docstring underlines normalised.
- `gen_wwminput` docstring now says the `&STATION` block is generated; a template missing a section is reported as an error instead of silently mis-assigning `BEGTC/ENDTC`.

## 2. Documented only

| ID | Note |
|----|------|
| A6 | `chain_hotstart: false` is for single-group tests. After the first group no `hotstart.nc` link exists, so later groups cannot start. |
| A8 | After restarting `submit_run`, diag jobs for already-complete stacks are re-submitted; they exit immediately because the per-variable `.done` markers exist. |
| A13 | `setup_run.done` makes `setup_run` skip a group. After changing `chain_hotstart`, `diag_run_plots`, the executable or any run card, delete `R{ID}_*/setup_run.done` and re-run `setup_run`. |

## 3. Reported — known limitations, intentionally unchanged

- **A4/A5 (auto_hotstart):** run completion is inferred from `mirror.out`/`myout` before the job leaves the queue (guarded by the hotstart-file check); the hang detector (no TIME STEP change between polls) can cancel a job that is writing restart files when the poll interval is short.
- **B1:** monthly grouping is still whole calendar months; a mid-month `start_date` is only warned about.
- **D1 (rest):** ERA5/GloFAS have no +1-day pad for the 25th sflux record of the final group (`gen_sflux` pads the last record); a partial current month/year is never refreshed.
- **D2 (rest):** a corrupt run split over a month boundary escapes the >3-day fatal rule; a repair at a month end cannot see the next month.
- **K1:** config merge is a shallow `dict.update` (a warning is printed on collisions).
- **E-series:** `layer: 0` in old-I/O plots is treated as surface (unused by any template); NDBC annual files for the current year are not refreshed once the year ends; ocean_time units are assumed MJD seconds in `wave_skill`.
- **W1:** the `&STATION` block of `wwminput.nml` is generated, not preserved from the template.
- **T1:** `tidal/tides.py` / `tpxo.py` (vendored from pyschism) have shared class-level state and unreachable branches; left untouched because they produce the validated tides.
- **Duplicated code kept on purpose:** `setup_run.py` is ~85 % duplicated between `schism`/`schism_wwm` and `ufs_schism`/`ufs_schism_ww3`; boundary loaders disagree on `hgrid.ll` vs `hgrid.gr3` priority. Merging them changes what runs and cannot be validated without HPC runs.
- **Style kept:** `print` with `[STEP]/[SKIP]/[N/A]/[NOTE]` tags (no `logging`); sentinel names unchanged (renaming would orphan `.done` files on existing runs); `{{MONTH}}` / `IS_LAST_MONTH` template keys and `--month` CLI flag kept for compatibility.

## 4. Post-audit finding: constant open-boundary files (2026-10-21)

The static audit above could not see this; it appeared while rebuilding the
executables on Hercules.

**Finding.** In M11, M12 and M14 (`I*_20250901`) `TEM_3D.th.nc` was exactly 10,
`SAL_3D.th.nc` 0, `uv3D.th.nc` 0 and `elev2D.th.nc` 0 (standard deviation 0); the
nudging files and the hotstart were fine. The `gen_3Dth` executable in `bin/`
had been built from a source with an active `lon=lon-360` / `xind=xind-360`
while mesh (`hgrid.ll` 157..220) and HYCOM (`xlon` 150..230) were both 0-360,
so every open-boundary node fell outside the shifted HYCOM grid and was filled
with the constants of `gen_3Dth_from_nc.in`. Nothing failed: a Fortran `stop`
exits 0 and the job script wrote the sentinel. The job scripts and tutorial
claimed the line was commented out.

**Evidence of the fix** (scratch tests on I11_20250901 with executables built
from the pristine upstream source by `workflow/build/hycom_patches.py`):
boundary T/S/SSH/speed within 0.025 degC / 0.016 psu / 0.002 m / 0.009 m/s of
the nearest HYCOM value; nudging files identical to M11's; hotstart byte for
byte identical to M11's; the shifted variant on this mesh stops with
`Lon must be increasing`; a floor of 0 degC (an old Bering edit) clamped 44 % of
the boundary nodes, so the stock -2 is used.

**Changes.** Executables are built by the workflow (`--phase build`) from the
pristine sources; the utilities' job scripts check `fort.11` and the outputs
before writing the sentinel; a longitude preflight selects the executable
(`hycom_lon_shift`); `plot_hycom_outputs` verifies and plots the inputs;
`ulimit -s unlimited` is set in the job scripts.

**Consequence.** M11 to M14 were forced with constant boundary T/S, no HYCOM
velocity and no HYCOM elevation. They must be rerun with regenerated inputs
before any comparison is trusted. (It does not distinguish M12 from M14, whose
boundary files were identical.)

**Open items.** The UFS+WW3 build through the workflow has not been run; M11 to
M14 have not been rerun; `fd_ufs.yaml` now comes from the built UFS-Coastal
version instead of the older reference copy; the shortwave handling of the DATM
path versus sflux should be checked (see `templates/README.md` and the notes in
the session record).
