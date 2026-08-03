# Tutorial — Running STOFS-AK on Hercules (felicioc)

This is a copy-paste-ready walkthrough for **my** Hercules setup. Paths and
settings below are hardcoded to my case:

- Repo clone:   `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow`
- Project dir:  `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK`
- Model:        `M01`  (so `M01/`, `I01/`, `R01/`, `P01/`, `D01/`)
- conda base:   `/work2/noaa/nos-surge/felicioc/envs/miniconda3`
- Envs:         `swf_main` (download + aggregate + SCHISM preproc), `swf_plot` (plotting)
- Dates:        `2024-09-07` → `2026-06-30` (monthly; Sep 7 = first full ESPC-D-V02 day)
- Domain:       lon 150–230, lat 45–78, `lon_reference: "360"`

Shortcuts used below (set these in every new shell before running):
```bash
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
```

---

## 0. One-time: clone the repo

```bash
cd /work2/noaa/nos-surge/felicioc/STOFS_3D_AK
git clone https://github.com/felicio93/stofs_ak_workflow
# later, to pull updates:
cd stofs_ak_workflow && git pull
```

---

## 1. One-time: create the config

The config must exist before any other step (including `--setup-envs`).

```bash
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config

mkdir -p $CFG
cp $WF/templates/config_example/*.yaml $CFG/
```

All templates are pre-filled with my values. Confirm they look right:
```bash
cat $CFG/project.yaml   # project_dir, dates, slurm block, executables block
cat $CFG/domain.yaml    # lon 150-230, lat 45-78, lon_reference "360",
                        # estuary_depth_threshold 3.0, plot ranges
cat $CFG/envs.yaml      # conda_base, swf_main / swf_plot
cat $CFG/schism.yaml    # T/S constants, open boundaries, dt, nbin/mne_bin
cat $CFG/steps.yaml     # all flags (set only the ones you want to run)
```

---

## 2. One-time: create the conda environments (on the DTN)

Env creation needs internet; run on the DTN.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config

# First time only: create swf_main manually (bootstrap).
conda create -y -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil nco cdo
conda activate swf_main

# Create/verify all envs in the config (creates swf_plot).
python $WF/orchestrator.py --setup-envs --config $CFG
```

---

## 3. One-time: initialize the directory tree

```bash
conda activate swf_main
python $WF/orchestrator.py --init --config $CFG
```

Creates under `$SWF_PROJ/M01/`:
```
fix/   bin/   logs/
raw/hycom/{ssh,ts,uv}/   raw/era5/
I01/I01_YYYYMM/   R01/R01_YYYYMM/   P01/P01_YYYYMM/   D01/D01_YYYYMM/   D01/logs/
```

Verify:
```bash
ls $SWF_PROJ/M01
ls $SWF_PROJ/M01/I01 | head
```

**Copy fixed mesh files into `fix/`:**
```bash
cp /path/to/hgrid.gr3  $SWF_PROJ/M01/fix/
cp /path/to/hgrid.ll   $SWF_PROJ/M01/fix/
cp /path/to/vgrid.in   $SWF_PROJ/M01/fix/
cp /path/to/TEM_nudge.gr3  $SWF_PROJ/M01/fix/
```

**Copy compiled _noscaling executables into `bin/`:**
```bash
cp /path/to/gen_hot_from_hycom_0_noscaling.exe  $SWF_PROJ/M01/bin/
cp /path/to/gen_3Dth_from_hycom_noscaling.exe   $SWF_PROJ/M01/bin/
cp /path/to/gen_nudge_from_hycom_noscaling.exe  $SWF_PROJ/M01/bin/
```

> **IMPORTANT:** Only use the `_noscaling` executables. They expect unpacked
> float data (our HYCOM files are unpacked at download with `ncpdq -U`). The
> stock SCHISM executables apply `scale_factor * 1e-3 + 20` — using them on
> already-unpacked data gives completely wrong values.
> The `lon=lon-360` line is also commented out — correct for our 0-360 mesh.

---

## 4. Step 1 — Download HYCOM (DTN only, internet required)

Edit `$CFG/steps.yaml` — set only `download_hycom: true`, all others `false`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main

nohup python $WF/orchestrator.py --run --config $CFG \
    > $SWF_PROJ/M01/logs/hycom_download.log 2>&1 &
tail -f $SWF_PROJ/M01/logs/hycom_download.log
```

The script downloads day-by-day and runs a stale-data check after each month
completes. It stops immediately if a month looks stale (see log for details).

- GOFS 3.1 `expt_93.0` covers up to **2024-09-04**
- **ESPC-D-V02** covers **2024-09-05 → present** (automatically selected)
- Download is resume-safe: existing valid files are skipped

Verify a downloaded file:
```bash
ncdump -h $SWF_PROJ/M01/raw/hycom/ts/ts_20241001.nc | grep -E "water_temp|salinity|depth|time ="
```

---

## 5. Step 2 — Aggregate into monthly SCHISM stacks (any node, interactive)

Edit `$CFG/steps.yaml` — set only `aggregate_hycom: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/hycom_aggregate.log
```

Writes `SSH_1.nc`, `TS_1.nc`, `UV_1.nc` into each `I01_YYYYMM/`.
TS is converted to potential temperature and renamed to `temperature`
(required by SCHISM Fortran).

**Critical check:**
```bash
ncdump -h $SWF_PROJ/M01/I01/I01_202410/TS_1.nc | grep -E "temperature|salinity|time ="
# Must show 'temperature' (not 'water_temp') and 'salinity'
```

---

## 6. Step 3 — Debug plots (SLURM job array, optional)

Edit `$CFG/steps.yaml` — set only `plotting_debug: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Submits one SLURM array task per month (uses `swf_plot` env).
Monitor:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/D01/logs/plot_1.out
ls -lh $SWF_PROJ/M01/D01/D01_202410/
# HYCOM_temperature_202410.gif  HYCOM_salinity_202410.gif  HYCOM_ssh_202410.gif
```

If jobs fail with OOM: increase `plot_mem` in `$CFG/project.yaml` (default 16G).

---

## 7. Phase 3 — SCHISM preprocessing (gen_estuary → gen_hotstart/gen_3Dth/gen_nudge)

### Step 7a — gen_estuary (once, interactive)

Creates `fix/estuary.gr3` (shallow nodes flagged for estuary T/S) and
generates the three Fortran `.in` control files in `bin/`.

Edit `$CFG/steps.yaml` — set only `gen_estuary: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/gen_estuary.log
```

Verify:
```bash
# estuary.gr3 should exist and match hgrid.gr3 node count
wc -l $SWF_PROJ/M01/fix/estuary.gr3
wc -l $SWF_PROJ/M01/fix/hgrid.gr3   # should match

# .in files should be in bin/
ls -lh $SWF_PROJ/M01/bin/*.in
cat $SWF_PROJ/M01/bin/gen_3Dth_from_nc.in
cat $SWF_PROJ/M01/bin/gen_hot_from_nc.in
cat $SWF_PROJ/M01/bin/gen_nudge_from_nc.in
```

### Step 7b — gen_hotstart (SLURM, once, first month only)

Creates `hotstart.nc` for the first month (`I01_202409/` or whichever
`start_date` month is in `project.yaml`).

Edit `$CFG/steps.yaml` — set only `gen_hotstart: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor and verify:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/logs/gen_hotstart.out
ls -lh $SWF_PROJ/M01/I01/I01_202409/hotstart.nc
cat $SWF_PROJ/M01/I01/I01_202409/gen_hotstart.done   # sentinel file
```

### Step 7c — gen_3Dth (SLURM array, every month)

Creates boundary forcing files (`elev2D.th.nc`, `uv3D.th.nc`, `TEM_3D.th.nc`,
`SAL_3D.th.nc`) for every month.

Edit `$CFG/steps.yaml` — set only `gen_3Dth: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
# Once done, check a month:
ls -lh $SWF_PROJ/M01/I01/I01_202410/
# expect: elev2D.th.nc  uv3D.th.nc  TEM_3D.th.nc  SAL_3D.th.nc  gen_3Dth.done

# Check for errors in SLURM logs:
cat $SWF_PROJ/M01/logs/gen_3Dth_1.err   # should be empty
```

If some months need to be re-run, delete their sentinel file and re-submit:
```bash
rm $SWF_PROJ/M01/I01/I01_202410/gen_3Dth.done
# set gen_3Dth: true in steps.yaml, re-run orchestrator -- only pending months submitted
```

### Step 7d — gen_nudge (SLURM array, every month)

Creates nudging timeseries (`TEM_nu.nc`, `SAL_nu.nc`) for every month.

Edit `$CFG/steps.yaml` — set only `gen_nudge: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
ls -lh $SWF_PROJ/M01/I01/I01_202410/
# expect: TEM_nu.nc  SAL_nu.nc  gen_nudge.done
cat $SWF_PROJ/M01/logs/gen_nudge_1.err   # should be empty
```

---

## Quick reference — which step, which node, which env

| Step | Flag | Node | Env | Internet |
|------|------|------|-----|----------|
| `--setup-envs` | — | DTN | swf_main | yes |
| `--init` | — | any | swf_main | no |
| `download_hycom` | Phase 1 | DTN | swf_main | yes |
| `aggregate_hycom` | Phase 2 | any | swf_main | no |
| `plotting_debug` (submit) | Phase 2 | login | swf_main | no |
| plotting jobs | Phase 2 | compute | swf_plot (auto) | no |
| `gen_estuary` | Phase 3A | any | swf_main | no |
| `gen_hotstart` (submit) | Phase 3B | login | swf_main | no |
| `gen_3Dth` (submit) | Phase 3C | login | swf_main | no |
| `gen_nudge` (submit) | Phase 3D | login | swf_main | no |
| gen_* SLURM jobs | Phase 3B-D | compute | none (Fortran) | no |

---

## Resuming / re-running steps

| Step | Resume behavior |
|------|----------------|
| `download_hycom` | Skips valid existing daily files; stops on stale month |
| `aggregate_hycom` | Skips months where `SSH_1/TS_1/UV_1.nc` already exist |
| `plotting_debug` | Re-submit; overwrites GIFs |
| `gen_estuary` | Skips if `estuary.gr3` and `bin/*.in` already exist |
| `gen_hotstart` | Skips if `gen_hotstart.done` exists |
| `gen_3Dth` | Skips months where `gen_3Dth.done` exists |
| `gen_nudge` | Skips months where `gen_nudge.done` exists |

To re-run a completed step, delete its sentinel/output and re-run:
```bash
# e.g. re-run gen_3Dth for October 2024:
rm $SWF_PROJ/M01/I01/I01_202410/gen_3Dth.done
# set gen_3Dth: true, run orchestrator
```
