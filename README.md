# STOFS-AK SCHISM Preprocessing Workflow

Automated preprocessing workflow for the SCHISM ocean circulation model,
targeting the Alaska/Bering Sea domain (STOFS-AK). This tool runs on NOAA
RDHPC Hercules and handles data downloading on the head/DTN node and
preprocessing on compute nodes via SLURM.

**Author:** Felicio Cassalho

---

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Repository Structure](#repository-structure)
4. [Project Directory Structure](#project-directory-structure)
5. [One-Time Setup](#one-time-setup)
6. [Starting a New Project](#starting-a-new-project)
7. [Configuration Reference](#configuration-reference)
8. [Running the Workflow](#running-the-workflow)
9. [Step-by-Step Development Status](#step-by-step-development-status)
10. [Monitoring and Logs](#monitoring-and-logs)
11. [Rerunning Failed Steps](#rerunning-failed-steps)
12. [Troubleshooting](#troubleshooting)

---

## Overview

The workflow is split into two phases:

| Phase | Where it runs | Internet required | Description |
|-------|--------------|-------------------|-------------|
| 1 | Head node / DTN | Yes | Download raw forcing data (HYCOM, ERA5, GloFAS) |
| 2 | Compute nodes (SLURM) | No | Preprocess inputs, one job per month |
| 3 | Login / any node | No | SCHISM preprocessing (bctides, source, hotstart, etc.) |

The orchestrator (`orchestrator.py`) reads your configuration, builds the
project directory tree, and dispatches each enabled step.

---

## Prerequisites

### System tools (must be available in your `$PATH` on Hercules)

- **NCO** (NetCDF Operators): `ncks`, `ncpdq`, `ncap2`, `ncrename`, `ncrcat`, `ncatted`
- **CDO** (Climate Data Operators): `cdo`
- **Python 3.8+** with the packages listed below

Check availability:
```bash
module load nco cdo   # or however Hercules loads them
ncks --version
cdo --version
```

### Python packages

Phase 1 runs inside a single conda environment (`swf_main`) that provides
both the Python dependencies and the NCO/CDO tools. See
[One-Time Setup](#one-time-setup) for the `conda create` command. The Python
dependencies are:
- `pyyaml`
- `python-dateutil`

---

## Repository Structure

```
stofs_ak_workflow/              <- git repo (clone this to Hercules)
├── orchestrator.py             <- main entry point
├── workflow/
│   ├── config.py               <- shared config loading + month enumeration
│   ├── mesh_parser.py          <- shared .gr3 parser (nodes, elements, boundaries)
│   ├── setup_envs.py           <- create/verify conda envs (--setup-envs)
│   ├── download_hycom.py       <- Phase 1: download raw HYCOM data
│   ├── download_era5.py        <- Phase 1b: download ERA5 monthly raw files
│   ├── download_glofas.py      <- Phase 1c: download GloFAS annual raw files
│   ├── aggregate_hycom.py      <- Phase 2a: daily -> monthly SCHISM stacks
│   ├── plot_hycom.py           <- Phase 2b worker: per-month debug GIFs
│   ├── submit_plots.py         <- Phase 2b launcher: SLURM job array
│   ├── gen_sflux.py            <- Phase 2c: ERA5 -> sflux files
│   ├── submit_era5.py          <- Phase 2c/d launchers: SLURM job arrays
│   ├── gen_estuary.py          <- Phase 3A: create estuary.gr3 + .in files
│   ├── gen_bctides.py          <- Phase 3B: TPXO9 -> bctides.in per month
│   ├── gen_source.py           <- Phase 3C: GloFAS -> source.nc per month
│   └── submit_hycom_utils.py   <- Phase 3D-F: SLURM jobs for SCHISM Fortran utils
├── tutorials/
│   └── hercules_walkthrough.md <- copy-paste steps for the real Hercules case
├── templates/
│   ├── config_example/         <- copy these to your project's config/ dir
│   │   ├── project.yaml        <- project ID, paths, dates, SLURM, executables
│   │   ├── domain.yaml         <- lon/lat bounds, lon_reference, estuary threshold
│   │   ├── steps.yaml          <- step enable/disable flags
│   │   ├── envs.yaml           <- conda env names per step
│   │   └── schism.yaml         <- SCHISM-specific T/S constants, open boundaries
│   └── slurm/
│       ├── plot_hycom.sbatch   <- SLURM array template for HYCOM plotting
│       ├── plot_sflux.sbatch   <- SLURM array template for sflux plotting
│       ├── gen_sflux.sbatch    <- SLURM array template for gen_sflux
│       ├── gen_hotstart.sbatch <- SLURM single-job template for gen_hotstart
│       ├── gen_3Dth.sbatch     <- SLURM array template for gen_3Dth
│       └── gen_nudge.sbatch    <- SLURM array template for gen_nudge
└── README.md
```

---

## Project Directory Structure

For a project with ID `01`, the workflow creates the following tree:

```
<project_dir>/
└── M01/
    ├── config/                     <- your edited YAML files (NOT in git)
    │   ├── project.yaml
    │   ├── domain.yaml
    │   ├── steps.yaml
    │   └── envs.yaml
    ├── fix/                        <- fixed mesh + static river files
    │   ├── hgrid.gr3
    │   ├── vgrid.in
    │   ├── drag.gr3
    │   ├── source_glofas.csv       <- GloFAS extraction points (id,lon,lat)
    │   ├── source_schism.csv       <- SCHISM injection points  (id,lon,lat)
    │   └── (other fixed SCHISM inputs)
    ├── bin/                        <- compiled Fortran executables for this project
    ├── raw/                        <- raw downloaded data (never modified after download)
    │   ├── hycom/
    │   │   ├── ssh/
    │   │   ├── ts/
    │   │   └── uv/
    │   ├── era5/
    │   │   └── {YYYY}/era5_YYYYMM.nc
    │   └── glofas/
    │       └── {YYYY}/glofas_YYYY.nc
    ├── I01/                        <- processed inputs (one directory per month)
    │   ├── I01_202409/
    │   │   ├── SSH_1.nc
    │   │   ├── TS_1.nc
    │   │   ├── UV_1.nc
    │   │   ├── bctides.in
    │   │   ├── source.nc           <- river discharge (from gen_source)
    │   │   └── sflux/
    │   └── ...
    ├── R01/                        <- SCHISM run directories (one per month)
    ├── P01/                        <- postprocessing output (one per month)
    └── D01/                        <- debug plots (one directory per month)
```

**Key rules:**
- `fix/` is the single source of truth for mesh files. Monthly input directories
  will contain **symlinks** to files in `fix/`, not copies.
- `raw/` is a permanent cache. Files there are never deleted or overwritten.
  If a download step is rerun, already-downloaded files are skipped.
- `bin/` holds compiled Fortran executables specific to this project.
- `I{ID}_YYYYMM/` files are named with the SCHISM stack convention
  (`SSH_1.nc`, `TS_1.nc`, `UV_1.nc`) expected by the `gen_*_from_hycom` tools.
- `D{ID}/` holds optional debug GIFs generated by the `plotting_debug` step.

---

## One-Time Setup

### 1. Clone the workflow repository

Clone it wherever you keep your project code (it does not have to be `$HOME`).
Throughout this README, `$WF` refers to the path of your clone.

```bash
git clone https://github.com/felicio93/stofs_ak_workflow
export WF=$(pwd)/stofs_ak_workflow    # adjust to your clone location
```

Running the orchestrator never writes anything into `$WF` — the repo is just
code. All generated data goes under `project_dir` (from `project.yaml`).

### 2. Create the workflow conda environments

The workflow uses two conda environments:
- `swf_main` — orchestrator + NCO/CDO + ERA5 download (`cdsapi`, `netcdf4`, `xarray`) + TPXO bctides (`scipy`)
- `swf_plot` — matplotlib/cartopy/xarray (plotting and mesh diagnostics)

The easiest way is the built-in `--setup-envs` command, which creates any
missing environments and verifies the libraries of existing ones. It needs
internet, so run it on the DTN.

> **ERA5 prerequisite:** `swf_main` includes `cdsapi` for ERA5 download.
> Before running `download_era5`, you need a free Copernicus CDS account
> and a `~/.cdsapirc` file on the DTN with your API key:
> ```
> url: https://cds.climate.copernicus.eu/api
> key: <your-uid>:<your-api-key>
> ```
> Register at https://cds.climate.copernicus.eu

Note: `--setup-envs` reads the config, so you must **create the config first**
(see "Starting a New Project" below), then run this. There is also a bootstrap
step — you need a python with `pyyaml` to run the orchestrator at all, so create
`swf_main` manually the very first time:

```bash
# 1. Create the config directory first (see "Starting a New Project"):
mkdir -p /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
cp $WF/templates/config_example/*.yaml \
   /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config/

# 2. Bootstrap swf_main manually the first time:
conda create -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil nco cdo cdsapi netcdf4 xarray scipy
conda activate swf_main

# 3. Create/verify all envs referenced by the config (creates swf_plot):
python $WF/orchestrator.py --setup-envs \
    --config /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
```

This ensures both `swf_main` and `swf_plot` exist with the correct packages
(defined in `workflow/setup_envs.py`). Re-run it any time to verify envs.

You do not activate `swf_plot` yourself; the SLURM plotting jobs call its
Python interpreter directly by full path.

### 3. Add the repo path to your shell (optional but convenient)

Add this to your `~/.bashrc`, pointing at your actual clone location:
```bash
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
```

---

## Starting a New Project

### 1. Create the config directory for your project

```bash
mkdir -p /work/noaa/nos-surge/USER/my_project/M01/config
cp $WF/templates/config_example/* \
   /work/noaa/nos-surge/USER/my_project/M01/config/
```

### 2. Edit the config files

Open each file and fill in the values for your project. See the full
[Configuration Reference](#configuration-reference) below.

```bash
cd /work/noaa/nos-surge/USER/my_project/M01/config
# edit project.yaml, domain.yaml, steps.yaml, envs.yaml
```

### 3. Run --init to create the directory tree

```bash
python $WF/orchestrator.py \
    --init \
    --config /work/noaa/nos-surge/USER/my_project/M01/config
```

This will print every directory it creates. Verify the output looks correct
before proceeding. The command is safe to re-run — it will not delete or
overwrite existing directories.

### 4. Populate fix/ and bin/

Copy your mesh and fixed input files:
```bash
cp /path/to/hgrid.gr3   /work/noaa/nos-surge/USER/my_project/M01/fix/
cp /path/to/vgrid.in    /work/noaa/nos-surge/USER/my_project/M01/fix/
# ... other fixed files
```

Copy your compiled Fortran executables:
```bash
cp /path/to/compiled/schism_tool  /work/noaa/nos-surge/USER/my_project/M01/bin/
```

---

## Configuration Reference

### project.yaml

| Field | Type | Description |
|-------|------|-------------|
| `project_id` | string | Two-digit ID, e.g. `"01"`. Determines `M01`, `I01`, etc. |
| `project_dir` | string | Absolute path to the parent project directory on HPC |
| `start_date` | string | Simulation start date, format `YYYY-MM-DD` |
| `end_date` | string | Simulation end date, format `YYYY-MM-DD` |
| `grouping` | string | Time grouping: `monthly` (only option currently) |

### domain.yaml

| Field | Type | Description |
|-------|------|-------------|
| `lon_min` | float | Western boundary of download domain (decimal degrees) |
| `lon_max` | float | Eastern boundary of download domain (decimal degrees) |
| `lat_min` | float | Southern boundary of download domain (decimal degrees) |
| `lat_max` | float | Northern boundary of download domain (decimal degrees) |
| `lon_reference` | string | `"360"` for 0-360 grid (Bering Sea model); `"180"` for -180 to 180 |

**Note on `lon_reference`:** The Bering Sea / Alaska model uses `"360"` because
the 180-degree meridian crosses the domain. Lon bounds in `domain.yaml` should
match this convention (e.g. `lon_min: 150.0`, `lon_max: 230.0`).

### steps.yaml

Each field is a boolean (`true` / `false`). Enable only the step you are
currently testing. Disable it and enable the next step once it is verified.

| Field | Phase | Description |
|-------|-------|-------------|
| `inspect_mesh` | 0 (SLURM) | Plot all fix/ input files + mesh resolution + vertical layers |
| `download_hycom` | 1 — DTN | Download daily HYCOM SSH, TS, UV into `raw/hycom/` |
| `download_era5` | 1 — DTN | Download ERA5 monthly raw files into `raw/era5/` |
| `download_glofas` | 1 — DTN | Download GloFAS annual discharge files into `raw/glofas/` |
| `aggregate_hycom` | 2 | Concatenate daily files into monthly NetCDF stacks |
| `plotting_debug` | 2 (SLURM) | HYCOM debug GIFs (temperature, salinity, ssh) |
| `gen_sflux` | 2 (SLURM) | ERA5 raw → sflux_air/prc/rad per month |
| `plot_sflux` | 2 (SLURM) | ERA5/sflux debug GIFs |
| `gen_estuary` | 3 — interactive | Create fix/estuary.gr3 + bin/*.in files (run once) |
| `gen_bctides` | 3 — interactive | TPXO9 → bctides.in per month |
| `gen_source` | 3 — interactive | GloFAS discharge → source.nc per month |
| `gen_hotstart` | 3 (SLURM) | Create hotstart.nc (first month only) |
| `gen_3Dth` | 3 (SLURM) | Create boundary th.nc files per month |
| `gen_nudge` | 3 (SLURM) | Create nudging nu.nc files per month |

### envs.yaml

| Field | Type | Description |
|-------|------|-------------|
| `conda_base` | string | Absolute path to your miniconda installation |
| `conda_envs.<step_name>` | string | Name of the conda environment for that step |

There are two usage patterns for `conda_envs`:

- **Phase 1 (run inside the env):** steps like `download_hycom` run inside the
  environment you activated on the DTN. The configured name is used only to
  **verify** the active environment matches (a soft warning on mismatch); it
  does not switch environments.
- **Phase 2 (call the env):** compute-node steps call a specific interpreter by
  full path, never via `conda activate`. The `base` environment uses
  `{conda_base}/bin/python`; named environments use
  `{conda_base}/envs/{env}/bin/python`.

---

## Running the Workflow

### Phase 1 — HYCOM download (DTN node, internet required)

The HYCOM OPeNDAP download requires external internet access, which on
Hercules is **only available on the Data Transfer Node (DTN)**. Regular login
and compute nodes cannot reach `tds.hycom.org`.

1. Connect to the DTN and activate your download environment:

```bash
ssh hercules-dtn.hpc.msstate.edu
conda activate swf_main        # env with pyyaml/dateutil/NCO/CDO
```

2. In `steps.yaml`, set `download_hycom: true` and all others `false`.
3. Run:

```bash
python $WF/orchestrator.py \
    --run \
    --config /work/noaa/nos-surge/USER/my_project/M01/config
```

The script performs a host check up front and will refuse to run if it does
not detect a DTN hostname (a substring `dtn`). If you are on a different
system whose transfer node is not named `dtn`, bypass the check with:

```bash
export ALLOW_NON_DTN=1
```

The script will loop over every day between `start_date` and `end_date`,
downloading SSH, TS, and UV files into:
```
M01/raw/hycom/ssh/ssh_YYYYMMDD.nc
M01/raw/hycom/ts/ts_YYYYMMDD.nc
M01/raw/hycom/uv/uv_YYYYMMDD.nc
```

**Resume behaviour:** If a valid (non-empty) file already exists for a given
day and variable, it is skipped. Incomplete files are automatically
re-downloaded. If the download is interrupted, simply re-run the same command
and it will pick up where it left off.

**HYCOM source mapping:** the correct HYCOM dataset is selected automatically
per date. GOFS 3.1 `expt_93.0` ends **2024-09-04**; dates on/after
**2024-09-05** are fetched from **ESPC-D-V02** (which stores temperature,
salinity, u, and v in separate files that are merged automatically). Older
dates map to earlier GOFS experiments back through the 53.X reanalysis.

**Stale-data sanity check:** after downloading, the workflow compares the first
and last day of each month. If their field means are identical, it prints a
STALE-DATA WARNING — a strong signal that an expired aggregation was echoing
its last timestep (the bug that motivated the full source mapping). A clean run
reports "Stale-data check passed".

### Phase 2a — Aggregate HYCOM into monthly stacks (interactive, any node)

Once the daily raw files are downloaded, aggregate them into monthly SCHISM
stack files. This runs interactively in `swf_main` (no internet needed) and
is fast (`ncrcat` + `cdo`).

1. In `steps.yaml`, set `download_hycom: false` and `aggregate_hycom: true`.
2. Run:

```bash
conda activate swf_main
python $WF/orchestrator.py \
    --run \
    --config /work/noaa/nos-surge/USER/my_project/M01/config
```

For each month it writes `SSH_1.nc`, `TS_1.nc`, `UV_1.nc` into
`I{ID}_YYYYMM/`. The TS file is converted to potential temperature
(`cdo adipot`) and its temperature variable is renamed to `temperature`, as
required by the SCHISM `gen_*_from_hycom` tools. Missing days are reported as
warnings; aggregation proceeds with whatever days are present. Already-
aggregated months are skipped (resume-safe).

### Phase 2b — Debug plots (SLURM job array, one task per month)

Generate GIF animations to sanity-check the aggregated HYCOM data.

1. In `steps.yaml`, set `aggregate_hycom: false` and `plotting_debug: true`.
2. Run (from a node with `sbatch`, e.g. a login node):

```bash
conda activate swf_main
python $WF/orchestrator.py \
    --run \
    --config /work/noaa/nos-surge/USER/my_project/M01/config
```

This renders a SLURM job array (one task per month) and submits it. Each task
runs on a compute node using the `swf_plot` environment and writes GIFs
(temperature, salinity, SSH) into `D{ID}_YYYYMM/`. SLURM settings (account,
partition, walltime, mail) come from the `slurm:` block in `project.yaml`.

Monitor with `squeue -u $USER`; logs are in `D{ID}/logs/`. The plotting worker
also reports if a month has fewer time records than calendar days (a signal
that some daily downloads are missing).

---

## Step-by-Step Development Status

| Step | Status | Notes |
|------|--------|-------|
| `--init` directory creation | ✅ Done | Creates I/R/P/D + fix/raw/bin/logs + raw/glofas/ |
| `--setup-envs` | ✅ Done | Creates/verifies swf_main + swf_plot |
| `inspect_mesh` | ✅ Done | Phase 0, SLURM, fix/ diagnostic plots |
| `download_hycom` | ✅ Done | Phase 1, DTN, GOFS 3.1 + ESPC-D-V02 |
| `download_era5` | ✅ Done | Phase 1b, DTN, CDS API monthly download |
| `download_glofas` | ✅ Done | Phase 1c, DTN, EWDS API annual download |
| `aggregate_hycom` | ✅ Done | Phase 2a, interactive, daily → monthly stacks |
| `plotting_debug` | ✅ Done | Phase 2b, SLURM array, HYCOM debug GIFs |
| `gen_sflux` | ✅ Done | Phase 2c, SLURM array, ERA5 → sflux files |
| `plot_sflux` | ✅ Done | Phase 2d, SLURM array, ERA5/sflux debug GIFs |
| `gen_estuary` | ✅ Done | Phase 3A, interactive, estuary.gr3 + .in files |
| `gen_bctides` | ✅ Done | Phase 3B, interactive, TPXO9 → bctides.in |
| `gen_source` | ✅ Done | Phase 3C, interactive, GloFAS → source.nc |
| `gen_hotstart` | ✅ Done | Phase 3D, SLURM single job, first month |
| `gen_3Dth` | ✅ Done | Phase 3E, SLURM array, boundary th.nc files |
| `gen_nudge` | ✅ Done | Phase 3F, SLURM array, nudging nu.nc files |
| `mesh_parser.py` | ✅ Done | Shared .gr3 parser used by all relevant steps |
| Wave boundary forcing | Planned | Spectral boundary conditions |

---

## Monitoring and Logs

The orchestrator prints all output to stdout. Save it under the project's
`M{ID}/logs/` directory (created by `--init`) so logs stay with the project:

```bash
python $WF/orchestrator.py --run \
    --config /path/to/M01/config \
    2>&1 | tee <project_dir>/M01/logs/download_$(date +%Y%m%d_%H%M%S).log
```

---

## Rerunning Failed Steps

1. Check the log for `ERROR:` lines to identify which days/variables failed.
2. Re-run the same command — already-downloaded files are skipped automatically.
3. If a step completed successfully and you want to skip it on the next run,
   set its flag to `false` in `steps.yaml`.

---

## Troubleshooting

### `ncks` command not found
Load the NCO module:
```bash
module load nco
```
Add the module load to your `~/.bashrc` or to the job submission script.

### `import yaml` fails
Install pyyaml:
```bash
conda install -c conda-forge pyyaml python-dateutil
```

### HYCOM download returns wrong date
This is a known issue with HYCOM THREDDS aggregations. The workflow
automatically fixes the time axis after unpacking (`ncpdq -U` corrupts it).
If you see unexpected time values in the output files, check that `ncpdq`
and `ncap2` are both from the same NCO version.

### Download fails on Attempt A and Attempt B
- Check that the HYCOM THREDDS server is reachable:
  ```bash
  curl -I https://tds.hycom.org
  ```
- You must be on a node with internet access (DTN on Hercules).
- HYCOM servers occasionally go down for maintenance. Wait and retry.

### `python-dateutil` not found
```bash
conda install -c conda-forge python-dateutil
```

### Directory already exists warning during --init
This is harmless. `--init` never deletes existing directories, so it is
safe to re-run after adding new months or correcting a config value.
