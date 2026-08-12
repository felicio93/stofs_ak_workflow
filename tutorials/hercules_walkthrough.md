# Tutorial — Running STOFS-AK on Hercules (felicioc)

Copy-paste-ready walkthrough for **my** Hercules setup. Paths and settings
below are hardcoded to my case:

- Repo clone:   `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow`
- Project dir:  `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK`
- Model:        `M01`  (so `M01/`, `I01/`, `R01/`, `P01/`, `D01/`)
- model_type:   `schism`
- conda base:   `/work2/noaa/nos-surge/felicioc/envs/miniconda3`
- Envs:         `swf_main` (download + aggregate + SCHISM preproc), `swf_plot` (plotting + mesh diagnostics)
- Dates:        `2024-09-07` → `2026-06-30` (monthly; Sep 7 = first full ESPC-D-V02 day)
- Domain:       lon 150–230, lat 45–78, `lon_reference: "360"`

Shortcuts used below (set these in **every new shell** before running):
```bash
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
```

> After `pip install -e $WF` (Step 2), the `stofs-ak` command is available in
> the active env. Every command below uses `stofs-ak`; you can equivalently run
> `python $WF/orchestrator.py` if you prefer.

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
# shared config templates:
cp $WF/templates/config_example/*.yaml $CFG/
# model-specific config (SCHISM):
cp $WF/workflow/models/schism/templates/config/schism.yaml $CFG/
```

Confirm the templates look right:
```bash
cat $CFG/project.yaml   # model_type: schism, project_dir, dates, slurm, executables
cat $CFG/domain.yaml    # lon 150-230, lat 45-78, lon_reference "360", plot ranges
cat $CFG/envs.yaml      # conda_base, swf_main / swf_plot
cat $CFG/schism.yaml    # T/S constants, open boundaries, dt, nbin/mne_bin
cat $CFG/steps.yaml     # all flags (set only the ones you want to run)
```

---

## 2. One-time: create conda envs + install the package (on the DTN)

Env creation needs internet; run on the DTN.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config

# First time only: create swf_main manually (bootstrap).
# Includes cdsapi+netcdf4+xarray (ERA5/GloFAS) and scipy (TPXO/bctides).
conda create -y -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil \
    nco cdo cdsapi netcdf4 xarray scipy pandas
conda activate swf_main

# Install the workflow package (editable). Exposes the `stofs-ak` command.
pip install -e $WF

# Create/verify all envs referenced by the config (creates swf_plot).
stofs-ak --setup-envs --config $CFG
```

**CDS/EWDS API credentials (one-time):** ERA5 uses the CDS endpoint; GloFAS uses
the EWDS endpoint. `cdsapi` reads credentials from `~/.cdsapirc`. The workflow
hard-codes the EWDS URL for GloFAS, so `.cdsapirc` only needs the CDS URL:

```bash
cat ~/.cdsapirc
# url: https://cds.climate.copernicus.eu/api
# key: <your-api-key>
```
Register at https://cds.climate.copernicus.eu if missing. The same key works
for both CDS and EWDS.

**GloFAS licence (one-time):** accept the CEMS-FLOODS licence in your browser
while logged in to Copernicus:
https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=download#manage-licences

---

## 3. One-time: initialize the directory tree

```bash
conda activate swf_main
stofs-ak --init --config $CFG
```

Creates under `$SWF_PROJ/M01/`:
```
fix/   bin/   logs/
raw/hycom/{ssh,ts,uv}/   raw/era5/   raw/glofas/
I01/I01_YYYYMM/   R01/R01_YYYYMM/   P01/P01_YYYYMM/
D01/D01_YYYYMM/   D01/D01_fix/   D01/logs/
```

Verify:
```bash
ls $SWF_PROJ/M01
ls $SWF_PROJ/M01/raw
ls $SWF_PROJ/M01/I01 | head
```

**Copy fixed mesh files into `fix/`:**
```bash
cp /path/to/hgrid.gr3              $SWF_PROJ/M01/fix/
cp /path/to/hgrid.ll               $SWF_PROJ/M01/fix/
cp /path/to/vgrid.in               $SWF_PROJ/M01/fix/
cp /path/to/TEM_nudge.gr3          $SWF_PROJ/M01/fix/
cp /path/to/SAL_nudge.gr3          $SWF_PROJ/M01/fix/
cp /path/to/albedo.gr3             $SWF_PROJ/M01/fix/
cp /path/to/diffmin.gr3            $SWF_PROJ/M01/fix/
cp /path/to/diffmax.gr3            $SWF_PROJ/M01/fix/
cp /path/to/watertype.gr3          $SWF_PROJ/M01/fix/
cp /path/to/shapiro.gr3            $SWF_PROJ/M01/fix/
cp /path/to/windrot_geo2proj.gr3   $SWF_PROJ/M01/fix/
cp /path/to/rough.gr3              $SWF_PROJ/M01/fix/   # or drag.gr3 / manning.gr3
```

**Copy compiled _noscaling executables into `bin/`:**
```bash
cp /path/to/gen_hot_from_hycom_0_noscaling.exe  $SWF_PROJ/M01/bin/
cp /path/to/gen_3Dth_from_hycom_noscaling.exe   $SWF_PROJ/M01/bin/
cp /path/to/gen_nudge_from_hycom_noscaling.exe  $SWF_PROJ/M01/bin/
```

> **IMPORTANT:** Only use the `_noscaling` executables. They expect unpacked
> float data (our HYCOM files are unpacked at download with `ncpdq -U`). Stock
> SCHISM executables apply `scale_factor * 1e-3 + 20` — using them on
> already-unpacked data gives wrong values. The `lon=lon-360` line is commented
> out — correct for our 0-360 mesh.

---

## 4. Step 0 — Mesh diagnostics (SLURM, run before anything else)

Diagnostic plots of all `fix/` input files, mesh resolution, and vertical
layers → `D01/D01_fix/`.

Edit `$CFG/steps.yaml` — set only `inspect_mesh: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
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
Re-run: `rm $SWF_PROJ/M01/D01/D01_fix/inspect_mesh.done` and resubmit.

---

## 5. Step 1 — Download HYCOM (DTN only, internet required)

Edit `$CFG/steps.yaml` — set only `download_hycom: true`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main

stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/hycom_download.log
```

Downloads day-by-day into `raw/hycom/{ssh,ts,uv}/`; resume-safe; runs a
stale-data check after each month and stops if a month looks stale.

Verify:
```bash
ncdump -h $SWF_PROJ/M01/raw/hycom/ts/ts_20241001.nc | grep -E "water_temp|salinity|time ="
```

---

## 5b. Step 1b — Download ERA5 (DTN only, internet required)

Edit `$CFG/steps.yaml` — set only `download_era5: true`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main

stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/era5_download.log
```

Downloads one file per month into `raw/era5/YYYY/era5_YYYYMM.nc`. Resume-safe.

Verify:
```bash
ncdump -h $SWF_PROJ/M01/raw/era5/2024/era5_202409.nc | grep -E "u10|t2m|time ="
```

---

## 5c. Step 1c — Download GloFAS (DTN only, internet required)

GloFAS is downloaded annually (one file per year). The EWDS endpoint is used
regardless of what URL is in `~/.cdsapirc`.

**Prerequisite:** accept the CEMS-FLOODS licence (one-time, in browser):
https://ewds.climate.copernicus.eu/datasets/cems-glofas-historical?tab=download#manage-licences

Edit `$CFG/steps.yaml` — set only `download_glofas: true`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main

stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/glofas_download.log
```

Downloads one file per year into `raw/glofas/YYYY/glofas_YYYY.nc`
(`avg_dis`, m³/s, 0.05° grid, clipped to the domain). Resume-safe.

Verify:
```bash
ncdump -h $SWF_PROJ/M01/raw/glofas/2025/glofas_2025.nc
# expect: avg_dis(valid_time, latitude, longitude)
```

---

## 6. Step 2 — Aggregate HYCOM into monthly SCHISM stacks (any node)

Edit `$CFG/steps.yaml` — set only `aggregate_hycom: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/hycom_aggregate.log
```

Writes `SSH_1.nc`, `TS_1.nc`, `UV_1.nc` into each `I01_YYYYMM/`. TS is
converted to potential temperature and renamed to `temperature`.

**Critical check:**
```bash
ncdump -h $SWF_PROJ/M01/I01/I01_202410/TS_1.nc | grep -E "temperature|salinity|time ="
# Must show 'temperature' (not 'water_temp') and 'salinity'
```

---

## 6b. Step 2 — Generate sflux files (SLURM array)

Edit `$CFG/steps.yaml` — set only `gen_sflux: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/logs/gen_sflux_1.out
ls $SWF_PROJ/M01/I01/I01_202409/sflux/
# expect: sflux_air_1.1.nc ... sflux_prc_1.1.nc ... sflux_rad_1.1.nc ...
#         sflux_inputs.txt  gen_sflux.done
```

Re-run a month: `rm $SWF_PROJ/M01/I01/I01_202410/sflux/gen_sflux.done`.

---

## 7. Step 2 — Debug plots (SLURM job arrays, optional)

HYCOM debug GIFs — set only `plotting_debug: true`; sflux GIFs — set only
`plot_sflux: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
ls -lh $SWF_PROJ/M01/D01/D01_202410/
# HYCOM_temperature_202410.gif  HYCOM_salinity_202410.gif  HYCOM_ssh_202410.gif
```

If OOM: increase `plot_mem` / `plot_sflux_mem` in `$CFG/project.yaml`.

---

## 8. Phase 3 — SCHISM preprocessing

### Step 3A — gen_estuary (once, interactive)

Creates `fix/estuary.gr3` and the three Fortran `.in` control files in `bin/`.
Edit `$CFG/steps.yaml` — set only `gen_estuary: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/gen_estuary.log
```

Verify:
```bash
wc -l $SWF_PROJ/M01/fix/estuary.gr3
wc -l $SWF_PROJ/M01/fix/hgrid.gr3   # node counts should match
ls -lh $SWF_PROJ/M01/bin/*.in
```

### Step 3B — gen_bctides (interactive, once per month)

TPXO9 → `bctides.in` per month. Uses `open_boundary_flags`,
`tidal_constituents`, `tobc`, `sobc` from `$CFG/schism.yaml`.

**Prerequisites:**
```bash
ls ~/.local/share/tpxo/            # expect: h_tpxo9.v1.nc  u_tpxo9.v1.nc
python -c "import scipy; print('scipy OK')"
```

Edit `$CFG/steps.yaml` — set only `gen_bctides: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/gen_bctides.log
```

Verify:
```bash
ls $SWF_PROJ/M01/I01/I01_*/bctides.done | wc -l   # should equal number of months
head -20 $SWF_PROJ/M01/I01/I01_202409/bctides.in
```

Re-run a month: `rm $SWF_PROJ/M01/I01/I01_202410/bctides.done`.

### Step 3C — gen_source (interactive, once per month)

GloFAS discharge → `source.nc` per month.

**Prerequisites — prepare two CSV files (one-time):**
1. In QGIS create `source_glofas.csv` (a point on each GloFAS 0.05° node to
   extract), columns `id,lon,lat`.
2. Copy it as `source_schism.csv`, moving each point to the injection location
   inside the SCHISM mesh (keep the same `id`).
3. Copy both to `fix/`:
```bash
cp /path/to/source_glofas.csv  $SWF_PROJ/M01/fix/
cp /path/to/source_schism.csv  $SWF_PROJ/M01/fix/
```

Edit `$CFG/steps.yaml` — set only `gen_source: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/gen_source.log
```

The script prints the distance from each `source_schism` point to the nearest
element centroid per month; `*** LARGE ***` (>0.5°) flags a suspect placement.

Verify:
```bash
ncdump -h $SWF_PROJ/M01/I01/I01_202409/source.nc
# expect: source_elem(nsources), vsource(time_vsource, nsources),
#         msource(time_msource, ntracers, nsources)
```

Re-run a month: `rm $SWF_PROJ/M01/I01/I01_202410/source.nc`.

### Step 3D — gen_param (interactive)

Writes `param.nml` per month with correct dates and hotstart chaining. Set only
`gen_param: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
```

### Step 3E — gen_hotstart (SLURM, once, first month only)

Set only `gen_hotstart: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/logs/gen_hotstart.out
ls -lh $SWF_PROJ/M01/I01/I01_202409/hotstart.nc
```

### Step 3F — gen_3Dth (SLURM array, every month)

Set only `gen_3Dth: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
ls -lh $SWF_PROJ/M01/I01/I01_202410/
# expect: elev2D.th.nc  uv3D.th.nc  TEM_3D.th.nc  SAL_3D.th.nc  gen_3Dth.done
```

Re-run a month: `rm $SWF_PROJ/M01/I01/I01_202410/gen_3Dth.done`.

### Step 3G — gen_nudge (SLURM array, every month)

Set only `gen_nudge: true`.

```bash
conda activate swf_main
stofs-ak --run --config $CFG
```

Monitor:
```bash
squeue -u $USER
ls -lh $SWF_PROJ/M01/I01/I01_202410/
# expect: TEM_nu.nc  SAL_nu.nc  gen_nudge.done
```

---

## 9. Phase 4 — Run management

### Step 9a — setup_run (interactive, fast)

Populates every `R01/R01_YYYYMM/` run directory: symlinks `fix/` mesh files and
`I01_YYYYMM/` inputs, copies the SCHISM executable, creates `outputs/` with
placeholder files (`staout_1..9`, `flux.out`, `combine_hotstart7.exe`), adapts
`run_test` and `run_comb` job cards with per-month names and the correct
hotstart step number, and renders `auto_hotstart.py` into each run directory.

**Prerequisites:**
- All Phase 3 preprocessing done for every month
- `fix/run_test` and `fix/run_comb` job card templates present in `M01/fix/`
- `bin/pschism_*` and `bin/combine_hotstart7.exe` present in `M01/bin/`
- Add the new executables block to `$CFG/project.yaml` if not already there:
  ```yaml
  executables:
    gen_hotstart: gen_hot_from_hycom_0_noscaling.exe
    gen_3Dth:     gen_3Dth_from_hycom_noscaling.exe
    gen_nudge:    gen_nudge_from_hycom_noscaling.exe
    schism:           pschism_HERCULES_NO_PARMETIS_PREC_EVAP_BLD_STANDALONE_SH_MEM_COMM_TVD-VL
    combine_hotstart: combine_hotstart7.exe
  ```
- Copy the updated `schism.yaml` template to your config if not already there
  (adds `chain_hotstart: true`):
  ```bash
  cp $WF/workflow/models/schism/templates/config/schism.yaml $CFG/schism.yaml
  ```

Edit `$CFG/steps.yaml` — set only `setup_run: true`.

```bash
conda activate swf_main
stofs-ak --run --phase run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/setup_run_$(date +%Y%m%d_%H%M%S).log
```

Verify:
```bash
ls -lh $SWF_PROJ/M01/R01/R01_202512/
# expect: hgrid.gr3 (symlink)  vgrid.in (symlink)  bctides.in (symlink)
#         param.nml (symlink)   source.nc (symlink)  sflux/ (symlink)
#         TEM_3D.th.nc (symlink)  SAL_3D.th.nc (symlink)  elev2D.th.nc (symlink)
#         uv3D.th.nc (symlink)  TEM_nu.nc (symlink)  SAL_nu.nc (symlink)
#         hotstart.nc (symlink, month 1 ONLY — -> I01_202512/hotstart.nc)
#         pschism_HERCULES_... (copied executable)
#         run_test  run_comb  auto_hotstart.py  outputs/  setup_run.done

# Job card names
grep "SBATCH -J" $SWF_PROJ/M01/R01/R01_202512/run_test    # -> R01_01
grep "SBATCH -J" $SWF_PROJ/M01/R01/R01_202601/run_test    # -> R01_02

# Combine step in run_comb
grep "combine_hotstart7" $SWF_PROJ/M01/R01/R01_202512/run_comb
# expect: ./combine_hotstart7.exe -i 44640   (step = nhot_write from param.nml)

# outputs/ placeholders
ls $SWF_PROJ/M01/R01/R01_202512/outputs/
# expect: staout_1 .. staout_9  flux.out  combine_hotstart7.exe

# Month 2 should NOT have hotstart.nc yet (chained at run time):
ls -la $SWF_PROJ/M01/R01/R01_202601/hotstart.nc  # should say "No such file"
```

Re-run a month: `rm $SWF_PROJ/M01/R01/R01_YYYYMM/setup_run.done`

---

### Step 9b — submit_run (blocking — run inside screen/tmux)

Calls `auto_hotstart.py` for the first pending month. That script:
1. Submits `run_test` via sbatch (the SCHISM MPI job)
2. Polls squeue every 120 s; checks `outputs/mirror.out` for TIME STEP
   advancement; cancels and resubmits if a hang is detected
3. When the run finishes ("Run completed successfully" in `mirror.out`):
   - Submits `run_comb` to run `combine_hotstart7.exe -i <nhot_write>` inside `outputs/`
   - Waits for the combine job to finish
   - Verifies `outputs/hotstart_it=<nhot_write>.nc` was created
   - Symlinks it as `R01_202601/hotstart.nc`
   - Writes `run.done` in this month's run dir
   - Launches `R01_202601/auto_hotstart.py` (if `chain_hotstart: true`)

**This command is BLOCKING.** Use `screen` so it survives a disconnected SSH
session:

```bash
screen -S stofs_run
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
export SWF_PROJ=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK
conda activate swf_main
```

Edit `$CFG/steps.yaml` — set `setup_run: false`, `submit_run: true`.

```bash
stofs-ak --run --phase run --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/submit_run_$(date +%Y%m%d_%H%M%S).log
# Ctrl-A D to detach   |   screen -r stofs_run to reattach
```

Monitor from another terminal while the run is going:
```bash
# Which jobs are running
squeue -u $USER

# Live auto_hotstart output for the current month
tail -f $SWF_PROJ/M01/R01/R01_202512/scrn.out

# SCHISM time stepping
tail -f $SWF_PROJ/M01/R01/R01_202512/outputs/mirror.out

# Which months have finished
ls $SWF_PROJ/M01/R01/R01_*/run.done

# Hotstart chaining check (after month 1 finishes)
ls -la $SWF_PROJ/M01/R01/R01_202601/hotstart.nc
# expect: symlink -> /work2/.../R01_202512/outputs/hotstart_it=44640.nc
```

**Resume behavior:** if your session drops, reattach with `screen -r stofs_run`
or re-run `stofs-ak --run --phase run --only submit_run --config $CFG` from a
fresh session. Months with `run.done` are skipped; the loop resumes from the
first pending month.

Re-run a specific month from scratch:
```bash
rm $SWF_PROJ/M01/R01/R01_202512/run.done
# Also remove the next month's hotstart symlink if it was already created:
rm -f $SWF_PROJ/M01/R01/R01_202601/hotstart.nc
stofs-ak --run --phase run --only submit_run --config $CFG
```

---

## 10. Phase 5 — Post-processing

All Phase 5 behavior is configured in `postprocess.yaml`. Copy the template
to your config directory (one-time):

```bash
cp $WF/workflow/models/schism/templates/config/postprocess.yaml $CFG/postprocess.yaml
```

Edit it to choose variables, layers, color scales, temporal cadence, the SST
matching mode, and frame retention. All plots overlay the 200 m and 2000 m
isobaths.

### Step 10a — diag_run_plots (per-stack diagnostics DURING the run)

Enable **before** `setup_run` so the hook is baked into each run directory's
`auto_hotstart.py`. As each SCHISM output stack (`out2d_N.nc` + its 3D
siblings) finishes, `auto_hotstart.py` submits a small SLURM job that writes
one diagnostic image per timestep per variable to
`D01/D01_YYYYMM/diag/`. No GIFs — this is for watching run health live.

```bash
# In steps.yaml: diag_run_plots: true   (set before running setup_run)
stofs-ak --run --phase run --only setup_run  --config $CFG
stofs-ak --run --phase run --only submit_run --config $CFG   # inside screen/tmux
# Watch:
ls $SWF_PROJ/M01/D01/D01_202512/diag/
```

### Step 10b — download_sst (DTN, satellite SST)

Downloads the LEO L3S-DY daily satellite SST and subsets it to your domain,
one file per day, into `M01/obs/sst_leo/`.

```bash
ssh hercules-dtn.hpc.msstate.edu
conda activate swf_main
# In steps.yaml: download_sst: true
stofs-ak --run --phase postprocess --only download_sst --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/download_sst_$(date +%Y%m%d_%H%M%S).log
# Verify:
ncdump -h $SWF_PROJ/M01/obs/sst_leo/leosst_20251201.nc   # vars: lon, lat, sst (degC)
```

### Step 10c — plot_outputs (full-run field GIFs)

Two SLURM jobs are submitted: a parallel array (one task per output file) that
renders frames, and a serial job (`afterok`) that assembles per-variable GIFs
into `P01/P01_plot_outputs/`.

```bash
conda activate swf_main
# In steps.yaml: plot_outputs: true
stofs-ak --run --phase postprocess --only plot_outputs --config $CFG
squeue -u $USER
ls $SWF_PROJ/M01/P01/P01_plot_outputs/*.gif
```

### Step 10d — compare_sst (model vs satellite SST GIF)

Requires `download_sst` to have run. Parallel per-day frames (model daily-mean
SST vs satellite, two panels) + serial GIF assembly into `P01/P01_compare_sst/`.

```bash
conda activate swf_main
# In steps.yaml: compare_sst: true
stofs-ak --run --phase postprocess --only compare_sst --config $CFG
squeue -u $USER
ls $SWF_PROJ/M01/P01/P01_compare_sst/compare_sst.gif
```

> **SST matching:** the LEO L3S-DY product is a daily collated field, so the
> model side is a **daily mean** of SCHISM surface temperature by default
> (`sst_match: daily_mean`). Switch to `nearest` in `postprocess.yaml` to use
> the model timestep closest to 12:00Z instead.

---

## Single-step override

To run one step regardless of the `steps.yaml` flags:

```bash
stofs-ak --run --only download_hycom --config $CFG
```

---

## Quick reference — step, node, env, internet

| Step | Phase | Node | Env | Internet |
|------|-------|------|-----|----------|
| `--setup-envs` | — | DTN | swf_main | yes |
| `--init` | — | any | swf_main | no |
| `inspect_mesh` (submit) | 0 | login | swf_main | no |
| inspect_mesh job | 0 | compute | swf_plot (auto) | no |
| `download_hycom` | 1 | DTN | swf_main | yes |
| `download_era5` | 1 | DTN | swf_main | yes |
| `download_glofas` | 1 | DTN | swf_main | yes |
| `aggregate_hycom` | 2 | any | swf_main | no |
| `gen_sflux` (submit) | 2 | login | swf_main | no |
| gen_sflux jobs | 2 | compute | swf_main (auto) | no |
| `plotting_debug` / `plot_sflux` (submit) | 2 | login | swf_main | no |
| plotting jobs | 2 | compute | swf_plot (auto) | no |
| `gen_estuary` / `gen_bctides` / `gen_source` / `gen_param` | 3 | any | swf_main | no |
| `gen_hotstart` / `gen_3Dth` / `gen_nudge` (submit) | 3 | login | swf_main | no |
| gen_* SLURM jobs | 3 | compute | none (Fortran) | no |
| `setup_run` | 4 | login | swf_main | no |
| `submit_run` (auto_hotstart loop) | 4 | login | swf_main | no |
| SCHISM MPI run | 4 | compute | none (MPI) | no |
| `run_comb` (combine hotstart) | 4 | compute | none (Fortran) | no |
| `diag_run_plots` jobs (during run) | 5 | compute | swf_plot (auto) | no |
| `download_sst` | 5 | DTN | swf_main | yes |
| `plot_outputs` frames | 5 | compute | swf_plot (auto) | no |
| `plot_outputs` GIF assembly | 5 | compute | swf_plot (auto) | no |
| `compare_sst` frames | 5 | compute | swf_plot (auto) | no |
| `compare_sst` GIF assembly | 5 | compute | swf_plot (auto) | no |

---

## Resuming / re-running steps

| Step | Resume behavior |
|------|----------------|
| `inspect_mesh` | Skips if `D01_fix/inspect_mesh.done` exists |
| `download_hycom` | Skips valid daily files; stops on stale month |
| `download_era5` | Skips existing monthly files; stops on stale month |
| `download_glofas` | Skips existing annual files; stops on stale year |
| `aggregate_hycom` | Skips months where `SSH_1/TS_1/UV_1.nc` exist |
| `gen_sflux` | Skips months where `sflux/gen_sflux.done` exists |
| `plotting_debug` | Re-submit; overwrites GIFs |
| `plot_sflux` | Skips months where `D01_YYYYMM/plot_sflux.done` exists |
| `gen_estuary` | Skips if `estuary.gr3` and `bin/*.in` exist |
| `gen_bctides` | Skips months where `bctides.done` exists |
| `gen_source` | Skips months where `source.nc` exists and is non-empty |
| `gen_hotstart` | Skips if `gen_hotstart.done` exists |
| `gen_3Dth` | Skips months where `gen_3Dth.done` exists |
| `gen_nudge` | Skips months where `gen_nudge.done` exists |
| `setup_run` | Skips months where `setup_run.done` exists |
| `submit_run` | Skips months where `run.done` exists; resumes from first pending month |
| `diag_run_plots` | Skips stacks where `D01_YYYYMM/diag/diag_<N>.done` exists |
| `download_sst` | Skips days where `obs/sst_leo/leosst_YYYYMMDD.nc` exists |
| `plot_outputs` | Re-submit; frames named by variable + timestamp; GIF in `P01_plot_outputs/` |
| `compare_sst` | Re-submit; frames in `P01_compare_sst/frames/`; GIF in `P01_compare_sst/` |

---

## HYCOM data notes

- **GOFS 3.1** (`expt_93.0`) covers up to **2024-09-04** — combined ts3z/uv3z.
- **ESPC-D-V02** covers **2024-09-05 → present** — per-day archive files. The
  annual `t3z/YYYY` aggregations are rolling ~70-day windows and silently
  return stale data for older dates; always use archive files for historical
  ESPC data.
- The stale-data check (first vs last day of each month) catches this
  automatically and halts the download with a clear warning.
