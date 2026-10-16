# STOFS-AK Modeling Workflow

Automated **pre-processing**, **run management** and **post-processing**
workflow for the SCHISM ocean circulation model and its coupled variants
(SCHISM+WWM and UFS-Coastal DATM+SCHISM(+WW3)), targeting the Alaska /
Bering Sea domain (STOFS-AK). It runs on NOAA RDHPC Hercules: data downloads
run on the DTN, preprocessing/execution on compute nodes via SLURM.

**Author:** Felicio Cassalho

---

## Table of Contents

1. [Overview](#overview)
2. [Supported models](#supported-models)
3. [Package architecture](#package-architecture)
4. [Repository structure](#repository-structure)
5. [Project directory structure](#project-directory-structure)
6. [Prerequisites and installation](#prerequisites-and-installation)
7. [Starting a new project](#starting-a-new-project)
8. [Configuration reference](#configuration-reference)
9. [Steps by phase](#steps-by-phase)
10. [Running the workflow](#running-the-workflow)
11. [Run management in detail (Phase 4)](#run-management-in-detail-phase-4)
12. [Post-processing (Phase 5)](#post-processing-phase-5)
13. [Sentinels, resuming and `--refresh`](#sentinels-resuming-and---refresh)
14. [Conventions](#conventions)
15. [Adding a new model](#adding-a-new-model)
16. [Known limitations](#known-limitations)
17. [Troubleshooting](#troubleshooting)

See also `tutorials/hercules_walkthrough.md` (copy-paste walkthrough for a
real Hercules project) and `docs/AUDIT.md` (code review record).

---

## Overview

The workflow has three top-level phases, dispatched by a model **driver**
selected from `project.yaml` (`model_type`):

| Phase | CLI | What it does |
|-------|-----|--------------|
| **Preprocess** | `stofs-ak --run` (default) | Download forcing, generate every model input |
| **Run** | `stofs-ak --run --phase run` | Populate run directories, launch group runs, chain restarts automatically |
| **Postprocess** | `stofs-ak --run --phase postprocess` | Field GIFs, SST/Argo/altimetry comparison, station skill |

`--phase all` runs the three in sequence.

Preprocessing steps run in different execution contexts:

| Context | Where | Internet | Examples |
|---------|-------|----------|----------|
| Downloads | DTN | yes | `download_hycom`, `download_era5`, `download_glofas`, `download_sst`, `download_coops`, `download_ndbc`, `download_argo`, `download_altimetry` |
| Interactive | any node | no | `aggregate_hycom`, `gen_estuary`, `gen_bctides`, `gen_source`, `gen_param`, `gen_datm_in`, ... |
| SLURM | login node, runs on compute | no | `inspect_mesh`, `gen_sflux`, `gen_hotstart`, `gen_3Dth`, `gen_nudge`, `gen_datm`, `gen_esmf_mesh`, plot steps |

---

## Supported models

| `model_type` | Description | Driver | Status |
|--------------|-------------|--------|--------|
| `schism` | SCHISM standalone | `SchismDriver` | implemented |
| `schism_wwm` | SCHISM + WWM (internally coupled waves) | `SchismWwmDriver(SchismDriver)` | implemented |
| `ufs_schism` | UFS-Coastal: DATM + CMEPS + SCHISM (old I/O) | `UfsSchismDriver` | implemented |
| `ufs_schism_ww3` | UFS-Coastal: DATM + CMEPS + SCHISM + WW3 | `UfsSchismWw3Driver(UfsSchismDriver)` | implemented |
| `schism_mice` | SCHISM + MICE (sea ice) | `SchismMiceDriver(SchismDriver)` | placeholder: all phases raise `NotImplementedError` |

Which steps apply to which model is listed in [Steps by phase](#steps-by-phase).

---

## Package architecture

```
                       orchestrator.py  (stofs-ak CLI)
                                │   reads project.yaml -> model_type
                                ▼
                     workflow.models.base.make_driver
                                │
        ┌───────────────────────┼─────────────────────────┐
        ▼                       ▼                         ▼
   SchismDriver           UfsSchismDriver           SchismMiceDriver
        │                       │                    (placeholder)
 SchismWwmDriver        UfsSchismWw3Driver
        │                       │
   preprocess / run / postprocess  (per model)
        │
   ┌────┴───────────────────────────────────┐
   ▼                    ▼                   ▼
workflow.downloaders  workflow.diagnostics  workflow.models.<model>.*
 (HYCOM/ERA5/GloFAS)  (mesh, HYCOM, sflux,  (preprocess/run/postprocess
                       DATM debug plots)     + templates)
        │
   all built on workflow.core
   (config, mesh_parser, station_parser, plot_style, slurm, templating, environment)
```

Key ideas:

- A **driver** (`ModelDriver` subclass) owns everything about one model.
  `schism_wwm` and `ufs_schism_ww3` inherit their parent driver and only add
  or override what is wave-specific.
- **`workflow.core`** holds the shared plumbing: config loading and group
  enumeration, `.gr3` and `station.in` parsing, plotting style,
  `SlurmSubmitter` (render `{{KEY}}` templates + `sbatch`), environment
  guards (`check_dtn`, `is_dtn`, `check_cdsapi`, `env_python`, `setup_envs`).
- **Downloaders** and **diagnostics** are model-agnostic.
- **SLURM templates** and **model YAML templates** live inside each model
  package (`workflow/models/<model>/templates/`).

---

## Repository structure

```
stofs_ak_workflow/                      <- git repo (clone this to Hercules)
├── pyproject.toml                      <- installable package (pip install -e .)
├── orchestrator.py                     <- CLI entry point (stofs-ak)
├── README.md
├── docs/AUDIT.md                       <- code review record (fixed / documented / reported)
├── tutorials/hercules_walkthrough.md   <- copy-paste steps for the real Hercules case
├── utils/                              <- helper notebooks (GloFAS shapefile -> csv, shape -> station.in)
├── templates/config_example/           <- ready-to-copy config set per model
│   ├── schism/  schism_wwm/  ufs_schism/  ufs_schism_ww3/
│   │     each: project.yaml domain.yaml steps.yaml envs.yaml <model>.yaml postprocess.yaml
│   └── README.md
└── workflow/
    ├── core/            config.py, mesh_parser.py, station_parser.py,
    │                    plot_style.py, slurm.py, templating.py, environment.py
    ├── downloaders/     hycom.py, era5.py, glofas.py
    ├── diagnostics/     inspect_mesh.py, plot_hycom.py, plot_sflux.py,
    │                    plot_datm.py, submit_*.py
    ├── tidal/           tides.py, tpxo.py (TPXO9 reader, vendored from pyschism)
    └── models/
        ├── base.py                     ModelDriver ABC + make_driver()
        ├── schism/                     driver, preprocess/, run/, postprocess/,
        │                               templates/{slurm,config,auto_hotstart.py}
        ├── schism_wwm/                 driver, preprocess/ (gen_wwmbnd, gen_wwminput),
        │                               run/setup_run.py, postprocess/ (wave_skill,
        │                               altimetry), templates/
        ├── ufs_schism/                 driver, preprocess/ (DATM, ESMF mesh, UFS
        │                               config files), run/, templates/
        ├── ufs_schism_ww3/             driver, preprocess/ (gen_ww3_shel/ounp),
        │                               run/setup_run.py, postprocess/ (wave_skill,
        │                               altimetry, plot_outputs_ww3), templates/
        └── schism_mice/                placeholder driver
```

---

## Project directory structure

For project ID `01` the workflow creates this tree under `project_dir`
(generated data, not part of the git repo):

```
<project_dir>/M01/
├── config/        your edited YAML files (not in git)
├── fix/           fixed mesh + static inputs (single source of truth; symlinked, not copied)
├── bin/           compiled executables (+ *.lua modulefiles for UFS)
├── raw/           raw downloads (hycom/{ssh,ts,uv}, era5/YYYY, glofas/YYYY); a permanent cache
├── obs/           observations (sst_leo, coops, ndbc, argo, altimetry)
├── I01/           processed inputs, one directory per group:  I01_YYYYMM/ (or I01_YYYYMMDD/)
├── R01/           run directories, one per group:             R01_YYYYMM/
├── D01/           debug plots: D01_YYYYMM/ per group, D01_fix/ (mesh), logs/
├── P01/           postprocessing output, one topic subdirectory per step:
│                  P01_plot_outputs/, P01_compare_sst/, P01_station_skill/,
│                  P01_collocate_argo/, P01_collocate_altimetry/, P01_plot_outputs_ww3/
└── logs/          orchestrator + SLURM logs
```

Group IDs are `YYYYMM` for monthly grouping and `YYYYMMDD` (group start date)
for `ndays` grouping. Rerunning a download skips files already present.

---

## Prerequisites and installation

Two conda environments provide everything (created by `--setup-envs`; the
package specs live in `workflow/core/environment.py`):

- `swf_main`: orchestrator, NCO/CDO, downloads (`cdsapi`, `netcdf4`, `xarray`),
  TPXO (`scipy`), `ocstrack`, `matplotlib`/`pandas`.
- `swf_plot`: plotting and SLURM worker jobs (`cartopy`, `imageio`,
  `mpi4py`, `gsw`, `dask`, ...). You never activate it; SLURM jobs call its
  interpreter by absolute path.

```bash
git clone https://github.com/felicio93/stofs_ak_workflow
export WF=$(pwd)/stofs_ak_workflow

conda create -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil \
    nco cdo cdsapi netcdf4 xarray scipy
conda activate swf_main
pip install -e $WF                       # exposes the `stofs-ak` command

stofs-ak --setup-envs --config <cfg>     # on the DTN; creates/verifies swf_plot
```

ERA5/GloFAS need a `~/.cdsapirc` on the DTN with your Copernicus key
(`url: https://cds.climate.copernicus.eu/api`, `key: <uid>:<api-key>`).

Running the tool never writes into `$WF`; all generated data goes under
`project_dir`.

---

## Starting a new project

```bash
mkdir -p $CFG                                   # e.g. <project_dir>/M01/config
# copy the complete set for your model (schism | schism_wwm | ufs_schism | ufs_schism_ww3):
cp $WF/templates/config_example/schism/*.yaml $CFG/

stofs-ak --init --config $CFG     # safe to re-run; never deletes anything
```

Each folder holds `project.yaml`, `domain.yaml`, `steps.yaml`, `envs.yaml`,
the self-contained `<model>.yaml` and `postprocess.yaml`. They reproduce the
four validated reference runs (M11 schism, M12 ufs_schism, M13 schism_wwm,
M14 ufs_schism_ww3): same paths, short test period (`2025-09-01` ..
`2025-09-08`, `ndays` / `group_ndays: 2`) and every applicable step `true`.
Edit `project_id`, `project_dir`, dates, grouping and the step flags for your
own project; see `templates/config_example/README.md`.

Then populate `fix/` and `bin/`.

**Common `fix/` inputs (all models):** `hgrid.gr3`, `hgrid.ll`, `vgrid.in`,
`partition.prop`, `tvd.prop`, `albedo.gr3`, `diffmin.gr3`, `diffmax.gr3`,
`watertype.gr3`, `shapiro.gr3`, `windrot_geo2proj.gr3`, `rough.gr3`,
`TEM_nudge.gr3`, `SAL_nudge.gr3`, `station.in`, `param.nml`, `run_test`,
`run_comb`, `source_glofas.csv`, `source_schism.csv`. Missing optional files
are skipped with a NOTE by `setup_run`.

| Model | Extra `fix/` inputs | `bin/` |
|-------|---------------------|--------|
| `schism` | none | `gen_*_noscaling.exe`, SCHISM exe, `combine_hotstart7.exe` |
| `schism_wwm` | `wwminput.nml` template | same, WWM-enabled SCHISM exe (`executables.schism_wwm`) |
| `ufs_schism` | `datm_in`, `datm.streams`, `fd_ufs.yaml`, `noahmptable.tbl`, `model_configure` | `*.lua` modulefiles, UFS exe (`executables.ufs_schism`), `combine_output11_MPI`, `combine_hotstart7.exe` |
| `ufs_schism_ww3` | everything above + `ww3_shel.nml`, `ww3_ounp.inp`, `mod_def.ww3`, `namelists.nml`, `ww3_points.list`, `ST4TABUHF2.bin`, `ww3_ESMFmesh.nc`, `mesh.msh` | UFS+WW3 exe (`executables.ufs_schism_ww3`), optionally `ww3_ounp` |

The `_noscaling` HYCOM utilities expect **unpacked** float data (the
downloader unpacks with `ncpdq -U`); do not use the stock SCHISM utilities.

---

## Configuration reference

All files in the config directory are merged into one flat dict by
`workflow.core.config.load_config`:

`project.yaml` + `domain.yaml` + `steps.yaml` + `envs.yaml` +
`<model_type>.yaml` + optional `postprocess.yaml`.

Later files win; **nested blocks (`slurm:`, `executables:`, `conda_envs:`) are
replaced, not merged**, and a warning is printed if two files define the same
block. Only the active model's yaml is loaded: `ufs_schism_ww3.yaml` does
**not** inherit from `ufs_schism.yaml`, and the SCHISM keys of `schism.yaml`
are not loaded for derived models. All SCHISM keys have code defaults; copy
the ones you want to change into your `<model_type>.yaml`.

### project.yaml

| Field | Description |
|-------|-------------|
| `project_id` | two-digit string (`"01"`) -> `M01`, `I01`, ... |
| `model_type` | `schism`, `schism_wwm`, `ufs_schism`, `ufs_schism_ww3` (`schism_mice` = placeholder) |
| `project_dir` | absolute parent directory on HPC |
| `start_date` / `end_date` | inclusive, `YYYY-MM-DD` |
| `grouping` | `monthly` (default), `ndays`, or alias `daily` / `weekly` / `biweekly` |
| `group_ndays` | required with `grouping: ndays`; ignored (with a warning) when an alias is used |
| `slurm:` | account, partition, mail_user, per-step walltime/mem (see template) |
| `executables:` | executable names in `M{id}/bin/` (`gen_hotstart`, `gen_3Dth`, `gen_nudge`, and per model `schism` / `schism_wwm` / `ufs_schism` / `ufs_schism_ww3`, `combine_hotstart`, `combine_output`, `ww3_ounp`) |

With monthly grouping use a `start_date` on the 1st; a warning is printed
otherwise. SLURM site defaults (`nos-surge`, `hercules-2`, mail user) are
defined once in `workflow/core/slurm.py` and overridden by `slurm:`.

### domain.yaml

`lon_min/lon_max/lat_min/lat_max` (download box), `lon_reference` (`"360"`
or `"180"`; Bering Sea domains crossing 180 use `"360"`),
`estuary_depth_threshold`, plot ranges/colormaps.

### steps.yaml

Boolean flag per step, grouped by phase (see
[Steps by phase](#steps-by-phase)). Enable one step, test it, then the next.
`--only <step>` runs one step regardless of the flags and is validated
against the phase given with `--phase`.

### envs.yaml

`conda_base` and `conda_envs.<step>` -> `swf_main` / `swf_plot`. For
interactive steps the name is only checked against the active env (warning
on mismatch); SLURM steps call the env interpreter by full path.

### Model YAMLs

| File | Main keys |
|------|-----------|
| `schism.yaml` | `estuary_temp/sal`, `outside_temp/sal`, `open_boundary_flags`, `tobc`, `sobc`, `tidal_constituents`, `tidal_cutoff_depth`, `open_boundaries`, `hycom_dt`, `nudge_stride`, `nbin`, `mne_bin`, `chain_hotstart` |
| `schism_wwm.yaml` | `wwm_cutoff_freq`, `wwm_outstyle_station`, `wwm_lsp1d`, `wwm_lsp2d`, `wwm_deltc_hotfile`, `wwm_procname` |
| `ufs_schism.yaml` | DATM (`datm_subdir`, `datm_filename_template`, `dt_atmos`), coupling (`coupling_dt`, `*_model`, `cpl_mode`, `coupling_config`, `case_name`), PET lists (`med/atm/ocn_petlist_bounds`), OMP threads |
| `ufs_schism_ww3.yaml` | all of the above (self-contained) + `wav_model`, `wav_petlist_bounds`, `wav_omp_num_threads`, `med_pio_*`, `wav_mesh_file`, `wav_pio_*`, `ww3_point_output_dt`, `ww3_fix_files`, `chain_ww3_restart` |
| `postprocess.yaml` | isobaths, `diag_run_vars`, `plot_outputs_*`, `compare_sst_*`, `station_skill_*`, `coops_*`, `ndbc_*`, `argo_*`/`collocate_argo_*`, altimetry and WW3 plot keys (documented inline) |

`chain_hotstart` (default `true`): `true` chains group N -> N+1
automatically; `false` is for **single-group testing** only (later groups
have no `hotstart.nc` link and cannot start).

`chain_ww3_restart` (UFS+WW3, default `true`): copy the WW3 restart to the
next group; `false` cold-starts WW3 every group (DATM and mediator restarts
are still chained). Configs copied from an older template that contain
`chain_ww3_restart: false` keep cold-starting WW3.

---

## Steps by phase

Legend: **S** = schism, **W** = schism_wwm, **U** = ufs_schism, **R** =
ufs_schism_ww3 (WW3). Steps not valid for a model print `[N/A]`.

### Phase 0-3: preprocess (`stofs-ak --run`)

| Step | Context | Models | Description |
|------|---------|--------|-------------|
| `inspect_mesh` | SLURM | all | plots of `fix/` inputs, resolution, layers -> `D{ID}/D{ID}_fix/` (JPEG) |
| `download_hycom` | DTN | all | daily SSH/TS/UV via OPeNDAP; corrupt days (<=3 in a row) are interpolated |
| `download_era5` | DTN | all | monthly ERA5 (CDS API) |
| `download_glofas` | DTN | all | annual GloFAS discharge (EWDS) |
| `aggregate_hycom` | any | all | daily -> `SSH_1/TS_1/UV_1` stacks per group; missing days inside a group are interpolated (<=3 in a row), otherwise that stack fails |
| `plot_hycom`, `plot_sflux` | SLURM array | all | debug GIFs |
| `gen_sflux` | SLURM array | all | ERA5 -> sflux files per group |
| `gen_estuary` | interactive | all | `estuary.gr3` + `gen_*_from_nc.in` (once) |
| `gen_bctides` | interactive | all | `bctides.in` from TPXO9 |
| `gen_source` | interactive | all | `source.nc` from GloFAS |
| `gen_param` | interactive | all | `param.nml` per group (dates, `rnday`, `nhot_write`, `ihot`) |
| `gen_hotstart` | SLURM | all | `hotstart.nc` (first group only) |
| `gen_3Dth`, `gen_nudge` | SLURM array | all | `*.th.nc`, `TEM/SAL_nu.nc` |
| `gen_wwmbnd` | interactive | W | `fix/wwmbnd.gr3` (once) |
| `gen_wwminput` | interactive | W | `wwminput.nml` per group (`BEGTC/ENDTC` from `param.nml`) |
| `gen_datm`, `plot_datm` | SLURM | U, R | sflux -> CDEPS DATM forcing; debug GIFs |
| `gen_esmf_mesh` | SLURM | U, R | ESMF mesh of the DATM grid |
| `gen_datm_in`, `gen_datm_streams`, `gen_model_configure`, `gen_ufs_configure` | interactive | U, R | UFS config files per group |
| `copy_fd_ufs`, `copy_noahmptable`, `copy_modulefiles` | interactive | U, R | copy static UFS files into each `I{ID}_*` |
| `gen_ww3_shel`, `gen_ww3_ounp` | interactive | R | `ww3_shel.nml`, `ww3_ounp.inp` per group |

For UFS models a SLURM barrier waits for `gen_datm` + `gen_esmf_mesh` before
the interactive config steps that read their output (only when no `--only`).

### Phase 4: run (`--phase run`)

`setup_run` (interactive) and `submit_run` (blocking, use `screen`/`tmux`).

### Phase 5: postprocess (`--phase postprocess`)

| Step | Context | Models |
|------|---------|--------|
| `diag_run_plots` (enable **before** `setup_run`) | during run | S only (not W, not UFS) |
| `download_sst`, `compare_sst` | DTN / SLURM | all |
| `plot_outputs` | SLURM (frames + GIF) | all |
| `download_coops`, `download_ndbc`, `station_skill` | DTN / interactive | all |
| `download_argo`, `collocate_argo`, `plot_argo` | DTN / SLURM / interactive | all |
| `wave_skill` | interactive | W, R |
| `download_altimetry`, `collocate_altimetry`, `plot_altimetry` | DTN / SLURM / interactive | W, R |
| `plot_outputs_ww3` | SLURM | R |

---

## Running the workflow

```bash
stofs-ak --run --config $CFG                                  # preprocess (default)
stofs-ak --run --only download_hycom --config $CFG            # one step, ignores flags
stofs-ak --run --phase run         --only setup_run  --config $CFG
stofs-ak --run --phase run         --only submit_run --config $CFG   # blocking: screen/tmux
stofs-ak --run --phase postprocess --config $CFG
stofs-ak --run --phase all --config $CFG                      # preprocess -> run -> postprocess
stofs-ak --refresh --config $CFG                              # reset sentinels (see below)
```

Notes:

- Download steps refuse to run off a DTN (`export ALLOW_NON_DTN=1` to
  override); with `--phase all` on a login node they print `[N/A]` and
  continue.
- If a download finishes with failures, the preprocess phase stops with an
  error instead of continuing into aggregation. Re-run it (valid files are
  skipped).
- `download_hycom` (DTN, no `sbatch`) and `plot_hycom` (needs `sbatch`) cannot
  both succeed in one invocation on the same node.
- `--phase all` waits for the SLURM jobs of the preprocess phase and stops
  before the run phase if any of them did not COMPLETE (checked with
  `sacct`).
- Steps of another phase that are enabled in `steps.yaml` produce a warning;
  `--only` with a name that does not belong to the phase is an error.
- Save output with `... 2>&1 | tee <project_dir>/M01/logs/<name>_$(date +%Y%m%d_%H%M%S).log`.

---

## Run management in detail (Phase 4)

**`setup_run`** (interactive, fast) populates each `R{ID}_{group}/`:
symlinks from `fix/` and `I{ID}_{group}/`, copies the executable, creates
`outputs/` with placeholder files (`staout_1..20`, `flux.out`), adapts the
`run_test` / `run_comb` job cards (unique job names, `combine_hotstart -i
<nhot_write>`) and renders `auto_hotstart.py`. UFS models additionally link
`forcing/`, `modulefiles/`, create `RESTART/` and render
`run_combine_output.sbatch`; WW3 adds the WW3 fixed files, namelists and
`convert_ww3_sta.sbatch`. Requires `executables.<model>` and
`executables.combine_hotstart`; UFS models also need `combine_output11_MPI` in
`bin/` (name overridable with `executables.combine_output`). `setup_run.done`
makes it skip a group: **after changing `chain_hotstart`, `diag_run_plots`,
the executable or a run card, delete `R{ID}_*/setup_run.done` and re-run.**

**`submit_run`** (blocking) launches the first pending group's
`auto_hotstart.py`, which:

1. cleans stale `schout_*`, `combine_*.done`, `mirror.out`, `fatal.error`;
2. submits `run_test` and polls `squeue` (60 s x5, 5 min, 20 min, 30 min,
   then every 60 min); a failed `squeue` is retried and never read as
   "job finished";
3. detects a hang (no `TIME STEP` change between polls) and cancels;
4. after the job leaves the queue: a SCHISM/MPI error signature
   (`fatal.error`, `err2.out`, `ABORT`) aborts with the details; any other
   failure is resubmitted up to 5 times;
5. on completion combines hotstarts (`run_comb`), cleans partition files,
   UFS: combines all output stacks (`combine_output11_MPI`), WW3: converts
   point output (`ww3_ounp`);
6. chains to the next group (`chain_hotstart`): symlinks the combined
   `hotstart.nc`; WWM: hotfile; UFS: DATM + mediator restart + rpointer
   files; WW3: restart (`chain_ww3_restart`); then launches the next group.

If `submit_run` is interrupted, re-run it: complete groups (`run.done`) are
skipped and a finished-but-not-chained group is picked up.

---

## Post-processing (Phase 5)

Configured in `postprocess.yaml`. All field plots overlay the 200 m and
2000 m isobaths.

1. **`diag_run_plots`**: per-stack frames during the run (standalone
   SCHISM only), enable before `setup_run`. SCHISM+WWM and UFS models do not
   support in-run diagnostics (the flag is ignored with an `[N/A]` line).
2. **`download_sst` -> `compare_sst`**: LEO L3S-DY satellite SST vs model
   daily mean (two-stage SLURM: frames then GIF).
3. **`plot_outputs`**: full-run field GIFs (variable, layer, cadence from
   `postprocess.yaml`). `layer` is `surface` (last level), `bottom` (first
   wet level) or an integer index.
4. **`download_coops` / `download_ndbc` -> `station_skill`**: station time
   series and `skill_metrics.csv` under `P{ID}/P{ID}_station_skill/`.
5. **`download_argo` -> `collocate_argo` -> `plot_argo`**: OCSTrack 3-D
   collocation of T/S profiles.
6. **`wave_skill`** (W, R): NDBC Hs / period / direction vs WWM or WW3 stations
   (direction metrics are circular).
7. **`download_altimetry` -> `collocate_altimetry` -> `plot_altimetry`** (W, R).
8. **`plot_outputs_ww3`** (R): WW3 field GIFs.

### `station.in` convention

`download_coops`, `download_ndbc` and `station_skill` read the comment of
each `station.in` line. A station is used only when it is exactly
`![VARS],<id>,<SOURCE>,<name>`, e.g.
`1 199.496 55.332 0.0 ![WL,T],9459450,CO-OPS,Sand Point` or
`10 182.532 57.034 0.0 ![T],46035,NDBC,Central Bering Sea`. Lines without a
bracket or without the id/source/name triplet are ignored.

VARS tokens: `WL`/`ELEV` (water level, CO-OPS), `T`/`TEMP` (water
temperature), `S`/`SAL`/`SALINITY` (salinity, CO-OPS), `air_pressure`/`PATM`,
`wind`; wave tokens `HS`, `TM01`/`TM02`, `DM` for `wave_skill`. `SOURCE` is
`CO-OPS` or `NDBC`. The model value comes from the `staout_*` column equal to
the station's line index in `station.in` (SCHISM order: 1 elev, 2 air
pressure, 3 windx, 4 windy, 5 T, 6 S). Surface variables use the `z=0`
entry; T/S use the shallowest non-zero depth entry (fallback `z=0`). NDBC has
no salinity.

---

## Sentinels, resuming and `--refresh`

Most steps are resume-safe through `*.done` files and output-presence checks.
Re-run the same command; check logs for `ERROR:`.

| Location | Sentinels |
|----------|-----------|
| `I{ID}_{g}/` | `gen_hotstart.done`, `gen_3Dth.done`, `gen_nudge.done`, `bctides.done`, `sflux/gen_sflux.done`, `gen_wwminput.done`, `gen_ww3_shel.done`, `gen_ww3_ounp.done`, `{datm_subdir}/gen_datm.done`, `{datm_subdir}/gen_esmf_mesh.done`, `gen_datm_in.done`, `gen_datm_streams.done`, `gen_model_configure.done`, `gen_ufs_configure.done`, `copy_*.done` |
| `R{ID}_{g}/` | `setup_run.done`, `run.done`, `outputs/combine_output.done`, `convert_ww3_sta.done` |
| `D{ID}_{g}/` | `plot_hycom.done`, `plot_sflux.done`, `plot_datm.done`, `diag/diag_*.done` |
| `P{ID}/P{ID}_*/` | `plot_outputs.done`, `compare_sst.done`, `station_skill.done`, `wave_skill.done`, `collocate_argo.done`, `plot_argo.done`, `collocate_altimetry.done`, `plot_outputs_ww3.done`, `.frames_done` |

`stofs-ak --refresh` deletes the sentinels of the first three directory
types (except `combine_output.done`, `convert_ww3_sta.done` and the
`diag/` markers, which are intentionally kept because their inputs are
removed after the run) plus the `P{ID}` topic sentinels and the top-level
`inspect_mesh.done`. Raw data and generated NetCDF are never deleted.

HYCOM data quality: after each month the downloader scans for spatially
constant fields; up to 3 consecutive corrupt days are replaced by linear
interpolation (original kept as `*.nc.bad`, file marked
`repaired_by_workflow`), more stops the workflow. A stale-data check guards
against wrong-date HYCOM sources.

---

## Conventions

- **Group** = one run unit (`monthly` or `ndays`). Code uses `group_id`;
  legacy names remain in CLI flags (`--month`, accepted alongside `--group`)
  and in template keys (`{{MONTH}}`, `IS_LAST_MONTH`).
- **Template rendering**: `{{KEY}}` placeholders in `.sbatch` and
  `auto_hotstart.py`; leftover placeholders warn (sbatch) or abort
  (`auto_hotstart.py`).
- **Console output**: `print` with `[STEP]`, `[SKIP]`, `[N/A]`, `[NOTE]`
  tags; step results are summarised, command traces go to `M{ID}/logs/`.
- **Failures**: library code prints `ERROR:` and exits non-zero; resumable
  steps never delete valid outputs.
- **Defaults**: SLURM account/partition/mail user come from
  `workflow.core.slurm.DEFAULT_*`, overridden by `project.yaml: slurm:`.
- **Type hints**: `Optional[str]`, `Path` for paths.

---

## Adding a new model

1. Create `workflow/models/<name>/driver.py`. For SCHISM-based couplings
   inherit `SchismDriver` (see `SchismWwmDriver`); for UFS-based ones inherit
   `UfsSchismDriver` (see `UfsSchismWw3Driver`); override only what differs.
2. Register it in `workflow/models/base.py::make_driver` and add the name to
   `KNOWN_MODEL_TYPES` in `workflow/core/config.py` (both lists must match).
3. Add a config set `templates/config_example/<name>/` (project, domain, steps,
   envs, a self-contained `<name>.yaml` since only the active model's yaml is
   loaded, and postprocess) and run `python utils/check_config_templates.py`.
   List the SLURM template directory in `pyproject.toml` package-data.
4. Put SLURM templates under `templates/slurm/` and drive them with a
   `core.slurm.SlurmSubmitter`.
5. Add the new step names to `PREPROCESS_STEPS` / `POSTPROCESS_STEPS` in
   `orchestrator.py` and their sentinels to `_SENTINELS` /
   `reset_sentinels` if they should be reset by `--refresh`.

---

## Known limitations

See `docs/AUDIT.md` section 3 for details. In short: completion/hang
detection in `auto_hotstart.py` is heuristic; monthly grouping spans whole
calendar months; ERA5/GloFAS have no extra pad day for the last group; a
corrupt-day run crossing a month boundary is not caught by the >3-day rule;
`setup_run` is duplicated between `schism`/`schism_wwm` and
`ufs_schism`/`ufs_schism_ww3`; the vendored TPXO code is unchanged.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `stofs-ak: command not found` | `pip install -e $WF` in `swf_main`, or `python $WF/orchestrator.py ...` |
| `ncks` / `cdo` not found | activate `swf_main` or `module load nco cdo` |
| `import yaml` fails | `conda install -c conda-forge pyyaml python-dateutil` |
| Download "not a DTN" | run on the DTN, or `export ALLOW_NON_DTN=1` |
| `sbatch not found` | submit SLURM steps from a login node, not the DTN |
| `--only X` rejected | `X` belongs to another `--phase`; the message names it |
| Change to `chain_hotstart` / executable ignored | delete `R{ID}_*/setup_run.done` and re-run `setup_run` |
| `unreplaced placeholder` warning/error | a template key is missing from the Python substitutions; the message names the key |
| Download phase stops with failures | re-run the download; valid files are skipped |
| HYCOM stale-data warning | re-download that month |
