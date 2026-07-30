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

The orchestrator and download script require:
- `pyyaml`
- `python-dateutil`

Install into your conda base environment (once):
```bash
conda install -c conda-forge pyyaml python-dateutil
```

Or if using pip:
```bash
pip install pyyaml python-dateutil
```

---

## Repository Structure

```
stofs_ak_workflow/              <- git repo (clone this to Hercules)
├── orchestrator.py             <- main entry point
├── workflow/
│   ├── download_hycom.py       <- Phase 1: download raw HYCOM data
│   └── (future steps here)
├── templates/
│   ├── config_example/         <- copy these to your project's config/ dir
│   │   ├── project.yaml
│   │   ├── domain.yaml
│   │   ├── steps.yaml
│   │   └── envs.yaml
│   └── slurm/                  <- SLURM job templates (future)
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
    │   ├── I01_202410/             <- October 2024
    │   └── ...
    ├── R01/                        <- SCHISM run directories (one per month)
    │   ├── R01_202409/
    │   └── ...
    └── P01/                        <- postprocessing output (one per month)
        ├── P01_202409/
        └── ...
```

**Key rules:**
- `fix/` is the single source of truth for mesh files. Monthly input directories
  will contain **symlinks** to files in `fix/`, not copies.
- `raw/` is a permanent cache. Files there are never deleted or overwritten.
  If a download step is rerun, already-downloaded files are skipped.
- `bin/` holds compiled Fortran executables specific to this project.

---

## One-Time Setup

### 1. Clone the workflow repository

On the Hercules DTN or head node:
```bash
cd ~
git clone https://github.com/felicio93/stofs_ak_workflow
```

### 2. Verify Python dependencies

```bash
python -c "import yaml, dateutil; print('OK')"
```

If this fails, install the missing packages (see [Prerequisites](#prerequisites)).

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
| `conda_envs.<step_name>` | string | Name of the conda environment to use for that step |

The workflow always calls Python via the full path to the environment's
interpreter, never via `conda activate`. The `base` environment uses
`{conda_base}/bin/python`; named environments use
`{conda_base}/envs/{env}/bin/python`.
This is required for non-interactive HPC shells.

---

## Running the Workflow

### Phase 1 — HYCOM download (head node / DTN, internet required)

1. In `steps.yaml`, set `download_hycom: true` and all others `false`.
2. Run:

```bash
python ~/stofs_ak_workflow/orchestrator.py \
    --run \
    --config /work/noaa/nos-surge/USER/my_project/M01/config
```

The script will loop over every day between `start_date` and `end_date`,
downloading SSH, TS, and UV files into:
```
M01/raw/hycom/ssh/ssh_YYYYMMDD.nc
M01/raw/hycom/ts/ts_YYYYMMDD.nc
M01/raw/hycom/uv/uv_YYYYMMDD.nc
```

**Resume behaviour:** If a file already exists for a given day and variable,
it is skipped. If the download is interrupted, simply re-run the same command
and it will pick up where it left off.

**On Hercules specifically:** Run this on the DTN node (`hercules-dtn`) which
has external internet access. The regular head nodes may not.

---

## Step-by-Step Development Status

| Step | Status | Notes |
|------|--------|-------|
| `--init` directory creation | Done | |
| `download_hycom` | Done | Phase 1, head node |
| `aggregate_hycom` | Planned | Phase 2, concatenate daily → monthly |
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
