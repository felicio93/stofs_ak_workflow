# Tutorial — Running STOFS-AK on Hercules (felicioc)

This is a copy-paste-ready walkthrough for **my** Hercules setup. Paths and
settings below are hardcoded to my case:

- Repo clone:   `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow`
- Project dir:  `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK`
- Model:        `M01`  (so `M01/`, `I01/`, `R01/`, `P01/`, `D01/`)
- conda base:   `/work2/noaa/nos-surge/felicioc/envs/miniconda3`
- Envs:         `swf_main` (download + aggregate + SCHISM preproc), `swf_plot` (plotting + mesh diagnostics)
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
# Note: includes cdsapi+netcdf4+xarray (ERA5) and scipy (TPXO/bctides).
conda create -y -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil nco cdo cdsapi netcdf4 xarray scipy
conda activate swf_main

# Create/verify all envs in the config (creates swf_plot).
python $WF/orchestrator.py --setup-envs --config $CFG
```

**ERA5 prerequisite:** before running `download_era5`, verify your CDS API
credentials are in place on the DTN:
```bash
cat ~/.cdsapirc
# Should show:
# url: https://cds.climate.copernicus.eu/api
# key: <your-uid>:<your-api-key>
```
If the file is missing, register at https://cds.climate.copernicus.eu and
create the file with your UID and API key.

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
I01/I01_YYYYMM/   R01/R01_YYYYMM/   P01/P01_YYYYMM/
D01/D01_YYYYMM/   D01/D01_fix/   D01/logs/
```

Verify:
```bash
ls $SWF_PROJ/M01
ls $SWF_PROJ/M01/I01 | head
ls $SWF_PROJ/M01/D01
```

**Copy fixed mesh files into `fix/`:**
```bash
cp /path/to/hgrid.gr3          $SWF_PROJ/M01/fix/
cp /path/to/hgrid.ll           $SWF_PROJ/M01/fix/
cp /path/to/vgrid.in           $SWF_PROJ/M01/fix/
cp /path/to/TEM_nudge.gr3      $SWF_PROJ/M01/fix/
cp /path/to/SAL_nudge.gr3      $SWF_PROJ/M01/fix/
cp /path/to/albedo.gr3         $SWF_PROJ/M01/fix/
cp /path/to/diffmin.gr3        $SWF_PROJ/M01/fix/
cp /path/to/diffmax.gr3        $SWF_PROJ/M01/fix/
cp /path/to/watertype.gr3      $SWF_PROJ/M01/fix/
cp /path/to/shapiro.gr3        $SWF_PROJ/M01/fix/
cp /path/to/windrot_geo2proj.gr3  $SWF_PROJ/M01/fix/
cp /path/to/rough.gr3          $SWF_PROJ/M01/fix/   # or drag.gr3 / manning.gr3
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

## 4. Step 0 — Mesh diagnostics (SLURM, run before anything else)

Generates diagnostic TIFF plots of all `fix/` input files, mesh resolution,
and vertical layer distribution. Output → `D01/D01_fix/`.

Edit `$CFG/steps.yaml` — set only `inspect_mesh: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor and verify:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/logs/inspect_mesh.out
ls -lh $SWF_PROJ/M01/D01/D01_fix/
# expect: bathymetry.tiff  albedo.tiff  diffmin.tiff  diffmax.tiff
#         watertype.tiff   shapiro.tiff  windrot_geo2proj.tiff
#         bottom_friction.tiff  TEM_nudge.tiff  SAL_nudge.tiff
#         mesh_resolution.tiff  vertical_layers.tiff
#         (estuary.tiff once gen_estuary has run)
```

If OOM: increase `inspect_mem` in `$CFG/project.yaml` (default 16G).

Re-run: delete the sentinel and re-submit:
```bash
rm $SWF_PROJ/M01/D01/D01_fix/inspect_mesh.done
```

---

## 5. Step 1 — Download HYCOM (DTN only, internet required)

Edit `$CFG/steps.yaml` — set only `download_hycom: true`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main

python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/hycom_download.log
```

The script downloads day-by-day and runs a stale-data check after each month.
It stops immediately if a month looks stale.

- GOFS 3.1 `expt_93.0` covers up to **2024-09-04**
- **ESPC-D-V02** covers **2024-09-05 → present** using per-day archive files
- Download is resume-safe: existing valid files are skipped

Verify:
```bash
ncdump -h $SWF_PROJ/M01/raw/hycom/ts/ts_20241001.nc | grep -E "water_temp|salinity|depth|time ="
```

---

## 5b. Step 1b — Download ERA5 (DTN only, internet required)

ERA5 is downloaded monthly (one CDS API call per month). Each raw file covers
the full month at hourly resolution.

**Prerequisites (one-time):**
```bash
# Verify CDS API credentials on the DTN
cat ~/.cdsapirc
# Must show:
# url: https://cds.climate.copernicus.eu/api
# key: <uid>:<api-key>
# Register at https://cds.climate.copernicus.eu if needed.
```

Edit `$CFG/steps.yaml` — set only `download_era5: true`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main

python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/era5_download.log
```

Downloads one file per month into `raw/era5/YYYY/era5_YYYYMM.nc`.
Resume-safe: existing files are skipped. Stale-data check runs after each
month and stops immediately if all timesteps are identical.

Verify a downloaded file:
```bash
ncdump -h $SWF_PROJ/M01/raw/era5/2024/era5_202409.nc | grep -E "u10|t2m|time ="
```

---

## 6. Step 2 — Aggregate into monthly SCHISM stacks (any node, interactive)

Edit `$CFG/steps.yaml` — set only `aggregate_hycom: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/hycom_aggregate.log
```

Writes `SSH_1.nc`, `TS_1.nc`, `UV_1.nc` into each `I01_YYYYMM/`.
TS is converted to potential temperature and renamed to `temperature`.

**Critical check:**
```bash
ncdump -h $SWF_PROJ/M01/I01/I01_202410/TS_1.nc | grep -E "temperature|salinity|time ="
# Must show 'temperature' (not 'water_temp') and 'salinity'
```

---

## 6b. Step 2b — Generate sflux files (SLURM array)

Converts raw ERA5 monthly files into SCHISM sflux files (one per day per
variable type: air, prc, rad). Uses unpadded stack numbers matching current
SCHISM: `sflux_air_1.1.nc`, `sflux_air_1.2.nc`, etc.

Edit `$CFG/steps.yaml` — set only `gen_sflux: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/logs/gen_sflux_1.out
ls $SWF_PROJ/M01/I01/I01_202409/sflux/
# expect: sflux_air_1.1.nc ... sflux_air_1.30.nc
#         sflux_prc_1.1.nc ... sflux_rad_1.1.nc ...
#         sflux_inputs.txt  gen_sflux.done
```

Verify a sflux file:
```bash
ncdump -h $SWF_PROJ/M01/I01/I01_202409/sflux/sflux_air_1.1.nc | grep -E "uwind|vwind|prmsl|time ="
```

Re-run a specific month:
```bash
rm -rf $SWF_PROJ/M01/I01/I01_202410/sflux/gen_sflux.done
# set gen_sflux: true, run orchestrator
```

---

## 7. Step 3 — Debug plots (SLURM job array, optional)

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

## 8. Phase 3 — SCHISM preprocessing

### Step 8a — gen_estuary (once, interactive)

Creates `fix/estuary.gr3` and the three Fortran `.in` control files in `bin/`.

Edit `$CFG/steps.yaml` — set only `gen_estuary: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/gen_estuary.log
```

Verify:
```bash
wc -l $SWF_PROJ/M01/fix/estuary.gr3
wc -l $SWF_PROJ/M01/fix/hgrid.gr3   # node counts should match
ls -lh $SWF_PROJ/M01/bin/*.in
```

After `gen_estuary` completes, re-run `inspect_mesh` to get the estuary plot:
```bash
rm $SWF_PROJ/M01/D01/D01_fix/inspect_mesh.done
# set inspect_mesh: true in steps.yaml, run orchestrator
```

### Step 8b — gen_bctides (interactive, once per month)

Generates `bctides.in` for every month using TPXO9 tidal data.
Uses the open boundaries defined in `schism.yaml` (`open_boundary_flags`,
`tidal_constituents`, `tobc`, `sobc`).

**Prerequisites:**
- `fix/hgrid.ll` must be in place (should already be there from Step 3)
- TPXO9 files at `~/.local/share/tpxo/` (already there from previous pyschism use):
  ```bash
  ls ~/.local/share/tpxo/
  # expect: h_tpxo9.v1.nc  u_tpxo9.v1.nc
  ```
- `scipy` must be in `swf_main`:
  ```bash
  conda activate swf_main
  python -c "import scipy; print('scipy OK')"
  # if missing: conda install -c conda-forge scipy
  ```

Edit `$CFG/steps.yaml` — set only `gen_bctides: true`.
Also update `$CFG/schism.yaml` to confirm `open_boundary_flags`, `tobc`,
`sobc`, and `tidal_constituents` are correct for your mesh.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/gen_bctides.log
```

Verify:
```bash
# Check a bctides.in was created for each month
ls $SWF_PROJ/M01/I01/I01_202409/bctides.in
head -20 $SWF_PROJ/M01/I01/I01_202409/bctides.in

# Check all months have sentinels
ls $SWF_PROJ/M01/I01/I01_*/bctides.done | wc -l  # should equal number of months
```

Re-run a specific month:
```bash
rm $SWF_PROJ/M01/I01/I01_202410/bctides.done
# set gen_bctides: true, run orchestrator
```

### Step 8c — gen_hotstart (SLURM, once, first month only)

Edit `$CFG/steps.yaml` — set only `gen_hotstart: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/logs/gen_hotstart.out
ls -lh $SWF_PROJ/M01/I01/I01_202409/hotstart.nc
```

### Step 8d — gen_3Dth (SLURM array, every month)

Edit `$CFG/steps.yaml` — set only `gen_3Dth: true`.

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
ls -lh $SWF_PROJ/M01/I01/I01_202410/
# expect: elev2D.th.nc  uv3D.th.nc  TEM_3D.th.nc  SAL_3D.th.nc  gen_3Dth.done
cat $SWF_PROJ/M01/logs/gen_3Dth_1.err   # should be empty
```

Re-run a specific month:
```bash
rm $SWF_PROJ/M01/I01/I01_202410/gen_3Dth.done
# set gen_3Dth: true, run orchestrator
```

### Step 8e — gen_nudge (SLURM array, every month)

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

## Quick reference — step, node, env, internet

| Step | Flag | Node | Env | Internet |
|------|------|------|-----|----------|
| `--setup-envs` | — | DTN | swf_main | yes |
| `--init` | — | any | swf_main | no |
| `inspect_mesh` (submit) | Phase 0 | login | swf_main | no |
| inspect_mesh job | Phase 0 | compute | swf_plot (auto) | no |
| `download_hycom` | Phase 1 | DTN | swf_main | yes |
| `download_era5` | Phase 1b | DTN | swf_main | yes |
| `aggregate_hycom` | Phase 2 | any | swf_main | no |
| `gen_sflux` (submit) | Phase 2b | login | swf_main | no |
| gen_sflux jobs | Phase 2b | compute | swf_main (auto) | no |
| `plotting_debug` (submit) | Phase 2 | login | swf_main | no |
| `plot_sflux` (submit) | Phase 2c | login | swf_main | no |
| plotting/plot_sflux jobs | Phase 2 | compute | swf_plot (auto) | no |
| `gen_estuary` | Phase 3A | any | swf_main | no |
| `gen_bctides` | Phase 3B | any | swf_main | no |
| `gen_hotstart` (submit) | Phase 3C | login | swf_main | no |
| `gen_3Dth` (submit) | Phase 3D | login | swf_main | no |
| `gen_nudge` (submit) | Phase 3E | login | swf_main | no |
| gen_* SLURM jobs | Phase 3C-E | compute | none (Fortran) | no |

---

## Resuming / re-running steps

| Step | Resume behavior |
|------|----------------|
| `inspect_mesh` | Skips if `D01_fix/inspect_mesh.done` exists |
| `download_hycom` | Skips valid existing daily files; stops on stale month |
| `download_era5` | Skips existing monthly files; stops on stale month |
| `aggregate_hycom` | Skips months where `SSH_1/TS_1/UV_1.nc` exist |
| `gen_sflux` | Skips months where `I01_YYYYMM/sflux/gen_sflux.done` exists |
| `plotting_debug` | Re-submit; overwrites GIFs |
| `plot_sflux` | Skips months where `D01_YYYYMM/plot_sflux.done` exists |
| `gen_estuary` | Skips if `estuary.gr3` and `bin/*.in` already exist |
| `gen_bctides` | Skips months where `I01_YYYYMM/bctides.done` exists |
| `gen_hotstart` | Skips if `gen_hotstart.done` exists |
| `gen_3Dth` | Skips months where `gen_3Dth.done` exists |
| `gen_nudge` | Skips months where `gen_nudge.done` exists |

---

## HYCOM data notes

- **GOFS 3.1** (`expt_93.0`) covers up to **2024-09-04** — daily data, combined ts3z/uv3z.
- **ESPC-D-V02** covers **2024-09-05 → present** — per-day archive files
  (`US058GCOM-OPSnce.espc-d-031-hycom_fcst_glby008_{YYYYMMDD}12_t0000_{var}.nc`).
  The annual `t3z/YYYY` aggregations are **rolling ~70-day windows** — they
  silently return stale data for dates older than ~70 days. Always use the
  archive files for historical ESPC data.
- The stale-data check (first vs last day of each month) catches this
  automatically and halts the download with a clear warning.
