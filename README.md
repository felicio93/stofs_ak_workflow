# STOFS-AK Modeling Workflow

Automated **pre-processing**, **run management**, and **post-processing**
workflow for the SCHISM ocean circulation model (and future coupled /
UFS-Coastal variants), targeting the Alaska/Bering Sea domain (STOFS-AK). It
runs on NOAA RDHPC Hercules and handles data downloading on the head/DTN node
and preprocessing/execution on compute nodes via SLURM.

**Author:** Felicio Cassalho

---

## Table of Contents

1. [Overview](#overview)
2. [Package Architecture](#package-architecture)
3. [Repository Structure](#repository-structure)
4. [Project Directory Structure](#project-directory-structure)
5. [Prerequisites](#prerequisites)
6. [Installation](#installation)
7. [One-Time Setup](#one-time-setup)
8. [Starting a New Project](#starting-a-new-project)
9. [Configuration Reference](#configuration-reference)
10. [Running the Workflow](#running-the-workflow)
11. [Adding a New Model](#adding-a-new-model)
12. [Step-by-Step Development Status](#step-by-step-development-status)
13. [Monitoring, Logs, and Rerunning](#monitoring-logs-and-rerunning)
14. [Troubleshooting](#troubleshooting)

---

## Overview

The workflow is organized into **three top-level phases**, each dispatched by a
model **driver** selected from `project.yaml` (`model_type`):

| Phase | CLI | Status | Description |
|-------|-----|--------|-------------|
| **Preprocess** | `stofs-ak --run` (default) | ✅ Implemented for SCHISM | Download forcing, generate all model inputs |
| **Run** | `stofs-ak --run --phase run` | ✅ Implemented for SCHISM | Set up run dirs, launch monthly runs, chain hotstarts automatically |
| **Postprocess** | `stofs-ak --run --phase postprocess` | ✅ Field GIFs + SST comparison | Field output GIFs, model-vs-satellite SST; station/skill validation planned |

Within the preprocessing phase, steps run in different execution contexts:

| Context | Where it runs | Internet | Examples |
|---------|--------------|----------|----------|
| Downloads | Head node / DTN | Yes | `download_hycom`, `download_era5`, `download_glofas` |
| Processing | Any node | No | `aggregate_hycom`, `gen_bctides`, `gen_source` |
| SLURM jobs | Login node → compute | No | `inspect_mesh`, `plot_hycom`, `gen_sflux`, `gen_3Dth`, ... |

The orchestrator (`orchestrator.py`, exposed as the `stofs-ak` command) reads
your configuration, builds the project directory tree, and dispatches the
selected phase to the driver.

---

## Package Architecture

The code is organized so that **shared plumbing**, **data downloading**,
**model-agnostic diagnostics**, and **per-model logic** are cleanly separated.
Each model configuration is a **driver** (a subclass of `ModelDriver`) that
owns its preprocessing/run/postprocessing steps.

```
                       orchestrator.py  (stofs-ak CLI)
                                │
                                │  reads project.yaml -> model_type
                                ▼
                     workflow.models.base.make_driver
                                │
             ┌──────────────────┼───────────────────────┐
             ▼                  ▼                        ▼
       SchismDriver     SchismWwmDriver / ...      UfsCoastalDriver
             │           (inherit SchismDriver)     (inherit ModelDriver)
             │
   preprocess / run / postprocess
             │
   ┌─────────┼───────────────────────────────┐
   ▼         ▼                                 ▼
workflow.downloaders   workflow.diagnostics   workflow.models.schism.*
   (HYCOM/ERA5/GloFAS)  (mesh, HYCOM, sflux)  (preprocess/run/postprocess)
             │
        all built on
             ▼
        workflow.core   (config, mesh_parser, plot_style, slurm, environment)
```

**Key ideas**

- **Drivers** encapsulate everything about one model. Adding SCHISM+WWM is a
  small subclass that inherits all SCHISM steps and overrides only what's
  wave-specific.
- **`workflow.core`** holds all shared helpers so nothing is duplicated across
  steps: config loading, `.gr3` parsing, plotting style, SLURM submission
  (`SlurmSubmitter`), and environment guards (`check_dtn`, `check_cdsapi`,
  `env_python`, conda `setup_envs`).
- **Downloaders** and **diagnostics** are model-agnostic and reused by any
  driver.
- **SLURM templates** and **model-specific config** live inside each model
  package (`workflow/models/schism/templates/`).

---

## Repository Structure

```
stofs_ak_workflow/                      <- git repo (clone this to Hercules)
├── pyproject.toml                      <- installable package (pip install -e .)
├── orchestrator.py                     <- CLI entry point (stofs-ak); routes to a driver
├── README.md
├── tutorials/
│   └── hercules_walkthrough.md         <- copy-paste steps for the real Hercules case
├── utils/
│   └── glofas2schism_shp2csv.ipynb     <- helper notebook (GloFAS shapefile -> csv)
├── templates/
│   └── config_example/                 <- shared config templates (copy to your project)
│       ├── project.yaml                <- project id, model_type, paths, dates, SLURM
│       ├── domain.yaml                 <- lon/lat bounds, lon_reference, plot ranges
│       ├── steps.yaml                  <- per-step enable/disable flags (all phases)
│       └── envs.yaml                   <- conda env names per step
└── workflow/
    ├── __init__.py
    │
    ├── core/                           <- shared plumbing (used everywhere)
    │   ├── config.py                   <- YAML loading + month enumeration + progress/log
    │   ├── mesh_parser.py              <- .gr3 parser (nodes, elements, boundaries)
    │   ├── plot_style.py               <- shared plotting frames/GIF helpers
    │   ├── slurm.py                    <- SlurmSubmitter: render + sbatch templates
    │   └── environment.py              <- DTN/cdsapi/env checks + conda setup_envs
    │
    ├── downloaders/                    <- Phase 1: raw forcing data (model-agnostic)
    │   ├── hycom.py                    <- daily HYCOM SSH/TS/UV via OPeNDAP
    │   ├── era5.py                     <- ERA5 monthly raw files (CDS API)
    │   └── glofas.py                   <- GloFAS annual discharge (EWDS API)
    │
    ├── diagnostics/                    <- model-agnostic diagnostic plots
    │   ├── inspect_mesh.py             <- fix/ input plots + mesh resolution + layers
    │   ├── submit_inspect_mesh.py      <- SLURM launcher for inspect_mesh
    │   ├── plot_hycom.py               <- per-month HYCOM debug GIFs (worker)
    │   ├── submit_plots.py             <- SLURM array launcher for plot_hycom
    │   └── plot_sflux.py               <- per-month sflux debug GIFs (worker)
    │
    ├── tidal/                          <- TPXO9 reader (vendored from pyschism)
    │   ├── tides.py
    │   └── tpxo.py
    │
    └── models/                         <- per-model driver tree
        ├── base.py                     <- ModelDriver ABC + make_driver() factory
        │
        ├── schism/                     <- SCHISM standalone (implemented)
        │   ├── driver.py               <- SchismDriver: dispatches all phases
        │   ├── preprocess/
        │   │   ├── aggregate_hycom.py  <- daily -> monthly SCHISM stacks
        │   │   ├── gen_bctides.py      <- TPXO9 -> bctides.in per month
        │   │   ├── gen_estuary.py      <- estuary.gr3 + gen_*_from_nc.in files
        │   │   ├── gen_param.py        <- param.nml per month (dates, hotstart chain)
        │   │   ├── gen_sflux.py        <- ERA5 raw -> sflux files (SLURM worker)
        │   │   ├── submit_era5.py      <- SLURM launchers for gen_sflux / plot_sflux
        │   │   ├── gen_source.py       <- GloFAS discharge -> source.nc per month
        │   │   └── gen_hycom_utils.py  <- SLURM jobs for gen_hotstart/3Dth/nudge
        │   ├── run/
        │   │   └── run_manager.py      <- PLACEHOLDER: run submission/monitoring
        │   ├── postprocess/
        │   │   └── __init__.py         <- PLACEHOLDER: plots/validation/skill
        │   └── templates/
        │       ├── slurm/*.sbatch      <- SCHISM SLURM templates
        │       └── config/schism.yaml  <- SCHISM-specific config template
        │
        ├── schism_wwm/driver.py        <- PLACEHOLDER: SchismWwmDriver(SchismDriver)
        ├── schism_mice/driver.py       <- PLACEHOLDER: SchismMiceDriver(SchismDriver)
        └── ufs_coastal/driver.py       <- PLACEHOLDER: UfsCoastalDriver(ModelDriver)
```

---

## Project Directory Structure

For a project with ID `01`, the workflow creates the following tree under
`project_dir` (from `project.yaml`). This is **generated data**, not part of
the git repo.

```
<project_dir>/
└── M01/
    ├── config/                     <- your edited YAML files (NOT in git)
    │   ├── project.yaml
    │   ├── domain.yaml
    │   ├── steps.yaml
    │   ├── envs.yaml
    │   └── schism.yaml             <- model-specific (for model_type: schism)
    ├── fix/                        <- fixed mesh + static river files
    │   ├── hgrid.gr3
    │   ├── vgrid.in
    │   ├── drag.gr3
    │   ├── source_glofas.csv       <- GloFAS extraction points (id,lon,lat)
    │   ├── source_schism.csv       <- SCHISM injection points  (id,lon,lat)
    │   └── (other fixed SCHISM inputs)
    ├── bin/                        <- compiled Fortran executables for this project
    ├── raw/                        <- raw downloaded data (never modified after download)
    │   ├── hycom/{ssh,ts,uv}/
    │   ├── era5/{YYYY}/era5_YYYYMM.nc
    │   └── glofas/{YYYY}/glofas_YYYY.nc
    ├── I01/                        <- processed inputs (one directory per month)
    │   ├── I01_202409/
    │   │   ├── SSH_1.nc  TS_1.nc  UV_1.nc
    │   │   ├── bctides.in
    │   │   ├── source.nc
    │   │   └── sflux/
    │   └── ...
    ├── R01/                        <- SCHISM run directories (one per month)  [Phase 4]
    ├── P01/                        <- postprocessing output (one per month)   [Phase 5]
    ├── D01/                        <- debug plots (one directory per month)
    └── logs/                       <- orchestrator + SLURM logs
```

**Key rules:**
- `fix/` is the single source of truth for mesh files; monthly input dirs use
  **symlinks** to it, not copies.
- `raw/` is a permanent cache. Rerunning a download skips already-present files.
- `bin/` holds compiled Fortran executables specific to this project.
- `I{ID}_YYYYMM/` files use the SCHISM stack convention (`SSH_1.nc`, etc.)
  expected by the `gen_*_from_hycom` tools.

---

## Prerequisites

### System tools (available on Hercules, provided by conda envs below)

- **NCO** (`ncks`, `ncpdq`, `ncap2`, `ncrename`, `ncrcat`, `ncatted`, `ncwa`)
- **CDO** (`cdo`)
- **Python 3.8+**

### Python packages

Two conda environments provide everything (created via `--setup-envs`):
- `swf_main` — orchestrator + NCO/CDO + downloads (`cdsapi`, `netcdf4`,
  `xarray`) + TPXO bctides (`scipy`) + package deps (`pyyaml`, `python-dateutil`)
- `swf_plot` — plotting (`matplotlib`, `cartopy`, `xarray`, `imageio`,
  `pandas`, `numpy`)

---

## Installation

Clone the repo anywhere on Hercules (it does not have to be `$HOME`).
Throughout this README, `$WF` refers to the path of your clone.

```bash
git clone https://github.com/felicio93/stofs_ak_workflow
export WF=$(pwd)/stofs_ak_workflow    # adjust to your clone location
```

Install the package into your `swf_main` environment (editable, so code edits
take effect immediately). This exposes the `stofs-ak` console command and
removes any need for `sys.path` hacks:

```bash
conda activate swf_main
pip install -e $WF
```

After this you can run either `stofs-ak ...` or `python $WF/orchestrator.py ...`
— they are equivalent. Running the tool never writes into `$WF`; all generated
data goes under `project_dir`.

---

## One-Time Setup

### 1. Create the config directory for your project

```bash
mkdir -p /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
cp $WF/templates/config_example/*.yaml \
   /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config/
# For SCHISM, also copy the model-specific config template:
cp $WF/workflow/models/schism/templates/config/schism.yaml \
   /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config/
```

### 2. Bootstrap `swf_main` and install the package

```bash
conda create -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil \
    nco cdo cdsapi netcdf4 xarray scipy
conda activate swf_main
pip install -e $WF
```

### 3. Create/verify all conda envs referenced by the config

This creates `swf_plot` and verifies both envs. It needs internet → run on the
DTN.

```bash
stofs-ak --setup-envs \
    --config /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
```

Env package specs live in `workflow/core/environment.py`. Re-run any time to
verify. You never activate `swf_plot` yourself; SLURM jobs call its interpreter
by full path.

> **ERA5/GloFAS prerequisite:** create a `~/.cdsapirc` on the DTN with your
> Copernicus key:
> ```
> url: https://cds.climate.copernicus.eu/api
> key: <your-uid>:<your-api-key>
> ```
> Register at https://cds.climate.copernicus.eu (GloFAS uses the EWDS endpoint
> automatically).

---

## Starting a New Project

### 1. Edit the config files

```bash
cd /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
# edit project.yaml (set model_type, project_id, dates, paths, SLURM),
# domain.yaml, steps.yaml, envs.yaml, schism.yaml
```

### 2. Initialize the directory tree

```bash
stofs-ak --init \
    --config /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
```

Safe to re-run — it never deletes existing directories.

### 3. Populate `fix/` and `bin/`

```bash
cp /path/to/hgrid.gr3 /path/to/vgrid.in \
   /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/fix/
cp /path/to/compiled/*.exe \
   /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/bin/
```

---

## Configuration Reference

### project.yaml

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | string | Two-digit ID, e.g. `"01"`. Determines `M01`, `I01`, etc. |
| `model_type` | string | `schism` (implemented), `schism_wwm`, `schism_mice`, `ufs_coastal` (planned) |
| `project_dir` | string | Absolute path to the parent project directory on HPC |
| `start_date` / `end_date` | string | Simulation range, `YYYY-MM-DD` |
| `grouping` | string | Time grouping: `monthly` (only option currently) |
| `slurm:` | block | SLURM account/partition/walltime/mem per step |
| `executables:` | block | Fortran executable names in `M{id}/bin/` |

### domain.yaml

| Field | Type | Description |
|-------|------|-------------|
| `lon_min`/`lon_max`/`lat_min`/`lat_max` | float | Download bounding box (decimal degrees) |
| `lon_reference` | string | `"360"` (Bering Sea; 180° crosses domain) or `"180"` |
| `estuary_depth_threshold` | float | Depth (m) below which nodes are marked estuary |
| `plot_cmap`, `plot_*_range` | — | Debug plotting options |

### steps.yaml

Boolean flags grouped by phase. Enable one step, test it, disable it, enable
the next. Phase 0–3 = preprocessing; Phase 4 = run management (`setup_run`,
`submit_run`); Phase 5 = post-processing (placeholder). See the file's inline
comments for the full per-step list.

### envs.yaml

| Field | Description |
|-------|-------------|
| `conda_base` | Absolute path to your miniconda installation |
| `conda_envs.<step>` | Conda env name for that step (`swf_main` or `swf_plot`) |

### schism.yaml (model-specific)

Loaded automatically when present. Contains SCHISM T/S constants, open-boundary
flags, tidal constituents, nudging factors, hotstart binning parameters, etc.
Template: `workflow/models/schism/templates/config/schism.yaml`.

---

## Running the Workflow

All commands take `--config <dir>`. The **preprocess** phase is the default for
`--run`; the other phases are selected with `--phase`.

```bash
# Preprocessing (default phase). Enable steps in steps.yaml first.
stofs-ak --run --config <config_dir>

# Run a single step regardless of steps.yaml flags
stofs-ak --run --only download_hycom --config <config_dir>

# Phase 4 — populate run dirs (fast, interactive)
stofs-ak --run --phase run --only setup_run  --config <config_dir>

# Phase 4 — launch all monthly runs (blocking — use screen/tmux)
stofs-ak --run --phase run --only submit_run --config <config_dir>

# Phase 5 — post-processing (placeholder)
stofs-ak --run --phase postprocess --config <config_dir>

# Run all phases in sequence (preprocess -> run -> postprocess)
stofs-ak --run --phase all --config <config_dir>

# Reset all sentinels so the workflow re-runs from scratch on the next --run
stofs-ak --refresh --config <config_dir>
```

### Typical preprocessing sequence

1. **Downloads (DTN):** `download_hycom`, `download_era5`, `download_glofas`.
   Must run on the DTN (`ssh hercules-dtn...`, `conda activate swf_main`). The
   scripts refuse to run off a DTN unless `export ALLOW_NON_DTN=1`.
2. **Aggregate (any node):** `aggregate_hycom` builds monthly `SSH_1/TS_1/UV_1`
   stacks.
3. **SLURM steps (login node):** `inspect_mesh`, `plot_hycom`, `gen_sflux`,
   `plot_sflux`, `gen_hotstart`, `gen_3Dth`, `gen_nudge` — each renders a SLURM
   script and submits it. Monitor with `squeue -u $USER`; logs in `M{ID}/logs/`.
4. **Interactive SCHISM inputs:** `gen_estuary` (once), `gen_bctides`,
   `gen_source`, `gen_param`.

> **Note:** `download_hycom` (DTN, no `sbatch`) and `plot_hycom` (needs
> `sbatch`) cannot both succeed in one invocation on the same node. Run them
> separately; the driver warns if both are enabled.

### Typical run-phase sequence (Phase 4)

1. **`setup_run`** (interactive, fast): populates every `R{ID}_YYYYMM/`
   directory — symlinks from `fix/` and `I{ID}_YYYYMM/`, copies the SCHISM MPI
   executable, creates `outputs/` with placeholder files, adapts `run_test` and
   `run_comb` job cards, and renders `auto_hotstart.py` per month. Requires
   `executables.schism` and `executables.combine_hotstart` in `project.yaml`.
2. **`submit_run`** (blocking — run inside `screen`/`tmux`): calls
   `auto_hotstart.py` for month 1, which submits the SCHISM job, monitors
   `outputs/mirror.out` for progress and hangs, combines the end-of-month
   hotstart on completion, symlinks it into the next month's run directory, and
   launches that month automatically. Returns when all months are done.

> **`chain_hotstart`** in `schism.yaml` controls chaining: `true` (default) =
> auto-launch month N+1; `false` = stop after each month (useful for testing).
> See `tutorials/hercules_walkthrough.md` for the complete Phase 4 walkthrough.

### Typical postprocess sequence (Phase 5)

Configured in `postprocess.yaml` (variables, layers, cadence, color scales,
frame retention). All plots overlay the 200 m and 2000 m isobaths.

1. **`diag_run_plots`** (during the run, **enable before `setup_run`**):
   per-output-stack diagnostic frames (SSH/SST/SSS/U/V by default) written to
   `D{ID}/D{ID}_YYYYMM/diag/` as each stack is written. `auto_hotstart.py`
   submits a small SLURM job per completed stack. Static images, no GIF —
   for watching run health as it progresses.
2. **`download_sst`** (DTN): download + domain-subset the LEO L3S-DY daily
   satellite SST into `M{ID}/obs/sst_leo/`.
3. **`plot_outputs`**: full-run field GIFs (any configured variable/layer,
   every-X-hours cadence). Two-stage SLURM — parallel per-file frames, then a
   serial GIF-assembly job (`--dependency=afterok`). Output in `P{ID}/`.
 4. **`compare_sst`**: model (daily-mean SST) vs satellite two-panel GIF over a
    date range. Same two-stage SLURM pattern. Needs `download_sst` first.
 5. **`download_coops`** (DTN): parse `fix/station.in`, download NOAA CO-OPS
    station observations (water level, water temperature, air pressure, wind)
    into `M{ID}/obs/coops/` — one CSV per station/product/month (CO-OPS 6-min
    data is capped at one month per request). Resume-safe.
 6. **`download_ndbc`** (DTN): parse `fix/station.in`, download NOAA NDBC buoy
    observations (WTMP, wind, PRES, ...) into `M{ID}/obs/ndbc/` — one CSV per
    station/year. Past years come from the annual historical file; the current
    year is assembled from completed-month files + the realtime 45-day file.
 7. **`station_skill`** (interactive): compare CO-OPS and NDBC observations
    against the model `outputs/staout_*` at each station, write obs-vs-model
    time-series plots (bias/RMSE/R² in the legend) and a `skill_metrics.csv`
    under `P{ID}/P{ID}_station_skill/`. Only the variables named in each
    station's VARS bracket are assessed. Re-run for any sub-period via
    `station_skill_start`/`station_skill_end` in `postprocess.yaml` without
    re-downloading. Needs `download_coops` and/or `download_ndbc` first.

```bash
# Diagnostics: enable diag_run_plots in steps.yaml BEFORE setup_run.
# Then the standard Phase 5 steps:
stofs-ak --run --phase postprocess --only download_sst   --config <cfg>   # DTN
stofs-ak --run --phase postprocess --only plot_outputs   --config <cfg>
stofs-ak --run --phase postprocess --only compare_sst    --config <cfg>
stofs-ak --run --phase postprocess --only download_coops --config <cfg>   # DTN
stofs-ak --run --phase postprocess --only download_ndbc  --config <cfg>   # DTN
stofs-ak --run --phase postprocess --only station_skill  --config <cfg>
```

> **station.in convention.** `download_coops`, `download_ndbc`, and
> `station_skill` read the per-station comment in `fix/station.in`. A station
> is used only when its comment is exactly `![VARS],<id>,<SOURCE>,<name>` —
> e.g. `1 199.496 55.332 0.0 ![WL,T],9459450,CO-OPS,Sand Point` or
> `10 182.532 57.034 0.0 ![T],46035,NDBC,Central Bering Sea`. Lines without a
> bracket, or missing the id/source/name triplet, are ignored. VARS tokens:
> `WL`/`elev` (water level, CO-OPS only), `T` (water temperature),
> `air_pressure`/`PATM`, `wind`. `SOURCE` is `CO-OPS` or `NDBC`. The station's
> model value comes from the `staout_*` column matching its line position in
> `station.in` (SCHISM order: staout_1=elev, 2=air pressure, 3=windx, 4=windy,
> 5=T). NDBC maps WTMP→T, PRES→air_pressure, WSPD/WDIR→wind (u/v).

---

## Adding a New Model

1. Create `workflow/models/<name>/driver.py` with a driver class. For
   SCHISM-based couplings, inherit from `SchismDriver` and override only the
   steps that differ; for UFS-Coastal, inherit from `ModelDriver`.
2. Register the class in the registry inside
   `workflow/models/base.py::make_driver`, and add `<name>` to
   `KNOWN_MODEL_TYPES` in `core/config.py`.
3. If the model needs extra config keys, add a `<name>.yaml` template under
   `workflow/models/<name>/templates/config/`. It is loaded automatically by
   `core.config.load_config` when `model_type: <name>` and the file is present
   in the project's `config/` dir (only the active model's YAML is loaded).
4. Put any SLURM templates under `workflow/models/<name>/templates/slurm/` and
   drive them with a `core.slurm.SlurmSubmitter`.
5. Set `model_type: <name>` in `project.yaml`.

---

## Step-by-Step Development Status

| Step | Phase | Status |
|------|-------|--------|
| `--init` directory creation | — | ✅ Done |
| `--setup-envs` | — | ✅ Done |
| `--refresh` sentinel reset | — | ✅ Done |
| `inspect_mesh` | 0 | ✅ Done |
| `download_hycom` / `download_era5` / `download_glofas` | 1 | ✅ Done |
| `aggregate_hycom` | 2 | ✅ Done |
| `plot_hycom` / `plot_sflux` | 2 | ✅ Done |
| `gen_sflux` | 2 | ✅ Done |
| `gen_estuary` / `gen_bctides` / `gen_source` / `gen_param` | 3 | ✅ Done |
| `gen_hotstart` / `gen_3Dth` / `gen_nudge` | 3 | ✅ Done |
| `setup_run` (populate R dirs, job cards, auto_hotstart.py) | 4 | ✅ Done |
| `submit_run` (blocking month-by-month run + hotstart chaining) | 4 | ✅ Done |
| `diag_run_plots` (per-stack diagnostic frames during run) | 5 | ✅ Done |
| `download_sst` (LEO L3S-DY satellite SST download + subset) | 5 | ✅ Done |
| `plot_outputs` (full-run field GIFs) | 5 | ✅ Done |
| `compare_sst` (model vs satellite SST GIF) | 5 | ✅ Done |
| `download_coops` (CO-OPS station obs download) | 5 | ✅ Done |
| `download_ndbc` (NDBC buoy obs download) | 5 | ✅ Done |
| `station_skill` (CO-OPS/NDBC obs vs model plots + skill CSV) | 5 | ✅ Done |
| SCHISM+WWM / SCHISM+MICE / UFS-Coastal drivers | — | 🚧 Placeholder |

---

## Monitoring, Logs, and Rerunning

Save orchestrator output under the project's `M{ID}/logs/`:

```bash
stofs-ak --run --config /path/to/M01/config \
    2>&1 | tee <project_dir>/M01/logs/preprocess_$(date +%Y%m%d_%H%M%S).log
```

**Rerunning failed steps:** most steps are resume-safe via `*.done` sentinel
files and skip already-completed months. Just re-run the same command; check
logs for `ERROR:` lines. Set a step's flag to `false` once verified.

**Re-running from scratch:** to reset all sentinels and re-run the entire
workflow (raw data and NetCDF outputs are preserved):

```bash
stofs-ak --refresh --config <config_dir>
stofs-ak --run --phase all --config <config_dir>
```

`--refresh` prints every sentinel it deletes, then exits. It clears sentinels
in `I{ID}`, `R{ID}`, and `D{ID}` for all months, the top-level
`inspect_mesh.done`, and the postprocessing sentinels in `P{ID}` (including
`plot_outputs.done`, `.frames_done`, `compare_sst.done`, `station_skill.done`).
Raw downloaded files and generated NetCDF inputs are **not** deleted — steps
with output-presence checks (e.g. `aggregate_hycom`, `gen_sflux`) will skip
regenerating files that already exist.

---

## Troubleshooting

### `stofs-ak: command not found`
Install the package into the active env: `pip install -e $WF` (with
`swf_main` activated). Or invoke via `python $WF/orchestrator.py ...`.

### `ncks` / `cdo` command not found
Activate `swf_main` (which provides NCO/CDO), or `module load nco cdo`.

### `import yaml` fails
`conda install -c conda-forge pyyaml python-dateutil` (or `pip install -e $WF`).

### Download refuses to run ("not a DTN")
Run on the DTN: `ssh hercules-dtn.hpc.msstate.edu && conda activate swf_main`.
On other systems: `export ALLOW_NON_DTN=1`.

### `sbatch not found`
Submit SLURM steps from a login node, not the DTN.

### HYCOM corrupted daily files (constant-field detection)

After downloading each month, the workflow scans every daily file for
spatially-constant fields (std < 1e-6 over valid points at any depth level).
This detects failed HYCOM forecast cycles where a level is filled with a
uniform value. Behaviour:

- **1–3 consecutive bad days**: the corrupt file is renamed to
  `ts_YYYYMMDD.nc.bad` and replaced with a linearly interpolated file built
  from the nearest good neighbors. A `repaired_by_workflow` global attribute
  is added to the repaired file. A `WARNING:` line is printed to screen and
  the debug log.
- **More than 3 consecutive bad days**: the workflow stops with an `ERROR:`
  message listing the bad days and instructions to investigate the source.

### HYCOM download returns stale/wrong dates
The workflow auto-selects the correct HYCOM source per date and runs a
stale-data sanity check. If a month reports a STALE-DATA WARNING, re-download
that month. See `workflow/downloaders/hycom.py` for the epoch table.
