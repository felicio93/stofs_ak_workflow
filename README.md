# STOFS-AK SCHISM Preprocessing Workflow

Automated preprocessing workflow for the SCHISM ocean circulation model,
targeting the Alaska/Bering Sea domain (STOFS-AK). This tool runs on NOAA
RDHPC Hercules and handles data downloading on the head/DTN node and
preprocessing on compute nodes via SLURM.

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
| 1 | Head node / DTN | Yes | Download raw forcing data (HYCOM, ERA5) |
| 2 | Compute nodes (SLURM) | No | Preprocess inputs, one job per month |

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
│   ├── setup_envs.py           <- create/verify conda envs (--setup-envs)
│   ├── download_hycom.py       <- Phase 1: download raw HYCOM data
│   ├── aggregate_hycom.py      <- Phase 2a: daily -> monthly SCHISM stacks
│   ├── plot_hycom.py           <- Phase 2b worker: per-month debug GIFs
│   └── submit_plots.py         <- Phase 2b launcher: SLURM job array
├── tutorials/
│   └── hercules_walkthrough.md <- copy-paste steps for the real Hercules case
├── templates/
│   ├── config_example/         <- copy these to your project's config/ dir
│   │   ├── project.yaml
│   │   ├── domain.yaml
│   │   ├── steps.yaml
│   │   └── envs.yaml
│   └── slurm/
│       └── plot_hycom.sbatch   <- SLURM job-array template for plotting
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
    ├── fix/                        <- fixed mesh files, same for every month
    │   ├── hgrid.gr3
    │   ├── vgrid.in
    │   ├── drag.gr3
    │   └── (other fixed SCHISM inputs)
    ├── bin/                        <- compiled Fortran executables for this project
    ├── raw/                        <- raw downloaded data (never modified after download)
    │   ├── hycom/
    │   │   ├── ssh/
    │   │   │   ├── ssh_20240901.nc
    │   │   │   └── ...
    │   │   ├── ts/
    │   │   │   └── ts_20240901.nc
    │   │   └── uv/
    │   │       └── uv_20240901.nc
    │   └── era5/
    ├── I01/                        <- processed inputs (one directory per month)
    │   ├── I01_202409/             <- September 2024
    │   │   ├── SSH_1.nc            <- monthly stack (var surf_el)
    │   │   ├── TS_1.nc             <- monthly stack (potential temp + salinity)
    │   │   └── UV_1.nc             <- monthly stack (water_u, water_v)
    │   ├── I01_202410/             <- October 2024
    │   └── ...
    ├── R01/                        <- SCHISM run directories (one per month)
    │   ├── R01_202409/
    │   └── ...
    ├── P01/                        <- postprocessing output (one per month)
    │   ├── P01_202409/
    │   └── ...
    └── D01/                        <- debug plots (one directory per month)
        ├── logs/                   <- SLURM logs for plotting jobs
        ├── D01_202409/
        │   ├── HYCOM_temperature_202409.gif
        │   ├── HYCOM_salinity_202409.gif
        │   └── HYCOM_ssh_202409.gif
        └── ...
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

On the Hercules DTN or head node:
```bash
cd ~
git clone https://github.com/felicio93/stofs_ak_workflow
```

### 2. Create the workflow conda environments

The workflow uses two conda environments:
- `swf_main` — orchestrator + NCO/CDO (download + aggregate)
- `swf_plot` — matplotlib/cartopy/xarray (plotting_debug, runs on compute nodes)

The easiest way is the built-in `--setup-envs` command, which creates any
missing environments and verifies the libraries of existing ones. It needs
internet, so run it on the DTN:

```bash
# Bootstrap: you need at least a python with pyyaml to run the orchestrator.
# The first time, create swf_main manually, then let --setup-envs handle the rest:
conda create -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil nco cdo
conda activate swf_main

python ~/stofs_ak_workflow/orchestrator.py --setup-envs \
    --config /work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
```

This ensures both `swf_main` and `swf_plot` exist with the correct packages
(defined in `workflow/setup_envs.py`). Re-run it any time to verify envs.

You do not activate `swf_plot` yourself; the SLURM plotting jobs call its
Python interpreter directly by full path.

### 3. Add the repo to your PATH (optional but convenient)

Add this to your `~/.bashrc`:
```bash
export SCHISM_WF=$HOME/stofs_ak_workflow
```

---

## Starting a New Project

### 1. Create the config directory for your project

```bash
mkdir -p /work/noaa/nos-surge/USER/my_project/M01/config
cp ~/stofs_ak_workflow/templates/config_example/* \
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
python ~/stofs_ak_workflow/orchestrator.py \
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
| `download_hycom` | 1 (head node) | Download daily HYCOM SSH, TS, UV into `raw/hycom/` |
| `aggregate_hycom` | 2 (compute) | Concatenate daily files into monthly NetCDF in `I{id}_YYYYMM/` *(not yet implemented)* |

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
python ~/stofs_ak_workflow/orchestrator.py \
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
python ~/stofs_ak_workflow/orchestrator.py \
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
python ~/stofs_ak_workflow/orchestrator.py \
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
| `--init` directory creation | Done | Creates I/R/P/D + fix/raw/bin |
| `download_hycom` | Done | Phase 1, DTN node |
| `aggregate_hycom` | Done | Phase 2a, interactive, daily → monthly stacks |
| `plotting_debug` | Done | Phase 2b, SLURM array, debug GIFs |
| `nudging` (SCHISM) | Planned | Phase 2, Fortran + Python |
| `3D flux boundary` | Planned | Phase 2, Fortran |
| `2D flux boundary` | Planned | Phase 2, Fortran |

---

## Monitoring and Logs

Currently the orchestrator prints all output to stdout. To save a log:

```bash
python ~/stofs_ak_workflow/orchestrator.py --run \
    --config /path/to/config \
    2>&1 | tee download_$(date +%Y%m%d_%H%M%S).log
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
