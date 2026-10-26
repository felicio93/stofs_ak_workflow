# Tutorial — Running STOFS-AK on Hercules (felicioc)

Copy-paste-ready walkthrough for **my** Hercules setup. Paths and settings
below are hardcoded to my case:

- Repo clone:   `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow`
- Project dir:  `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK`
- Model:        `M01`  (so `M01/`, `I01/`, `R01/`, `P01/`, `D01/`)
- model_type:   `schism` (SCHISM+WWM, UFS-SCHISM and UFS-SCHISM+WW3 differences are in sections 11-14)
- Executables:  built by the workflow itself (`--phase build`, section 3b) from `build.yaml`
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

## 1. One-time: create the project (config + starter `fix/` files)

The config must exist before any other step (including `--setup-envs`).
`--new-project` copies the model's templates: `M01/config/*.yaml` and the
starter files of `M01/fix/` (`param.nml`, `run_test`, `run_comb`, ...).

```bash
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config

# model: schism | schism_wwm | ufs_schism | ufs_schism_ww3 (sections 12-14).
# Needs pyyaml: run it from swf_main (bootstrap in section 2, first command)
# or any env that has it; `stofs-ak` exists after `pip install -e $WF`.
python $WF/orchestrator.py --new-project --model schism --id 01 \
    --dir /work2/noaa/nos-surge/felicioc/STOFS_3D_AK
```

(Copying `$WF/templates/schism/config/*.yaml` into `$CFG` by hand works too,
but then you also have to copy `templates/schism/fix/*`.)

> The template `steps.yaml` has **every step of the model set to `true`** (it
> reproduces the reference run). This walkthrough enables one step at a time:
> set the flags you are not running yet to `false` (or use `--only <step>`,
> which ignores the flags). The template also carries the reference run's
> test dates (`2025-09-01` .. `2025-09-08`, 2-day groups) and slurm/mail
> settings; set the dates from the header of this tutorial. `--new-project`
> already set `project_id`, `project_dir` and the build `source_root`.

Confirm the templates look right:
```bash
cat $CFG/project.yaml   # model_type: schism, project_dir, dates, slurm, executables
cat $CFG/domain.yaml    # lon 150-230, lat 45-78, lon_reference "360", plot ranges
cat $CFG/envs.yaml      # conda_base, swf_main / swf_plot
cat $CFG/schism.yaml    # T/S constants, open boundaries, dt, hycom_lon_shift, nbin/mne_bin
cat $CFG/build.yaml     # machine, source_root, sources (repo + ref), targets
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

`--new-project` already copied the starter files `param.nml`, `run_test` and
`run_comb` into `fix/`. Review them (in the job cards `-N/-n` must match your
partitioning: compute cores of `partition.prop` + scribes), then check what
is still missing:

```bash
stofs-ak --check-fix --config $CFG
# Required: ... MISSING ...  -> copy those files; Expected: setup_run skips a missing one with a NOTE
```

### 3b. Build the executables

`bin/` is filled by the build phase from `$CFG/build.yaml`: SCHISM (and its
HYCOM, combine and partition utilities for this model) are cloned/fetched into
`build.source_root`, compiled by one SLURM job per target, and installed into
`$SWF_PROJ/M01/bin/` under the names of `executables:` in `project.yaml`.
Run it on a login node (git is only available there); use `screen`/`tmux` the
first time:

```bash
conda activate swf_main
stofs-ak --run --phase build --config $CFG
squeue -u $USER
tail -n 3 $SWF_PROJ/M01/logs/build_*.out      # each ends with "=== build <target> complete ==="
ls -l $SWF_PROJ/M01/bin
head -40 $SWF_PROJ/M01/bin/BUILD_INFO.yaml    # commit, options, modules, sha256 of each file
```

A second run says `up to date ... skipping` for every target. To rebuild one
target delete `$SWF_PROJ/M01/build/<target>.done` (or change its block in
`build.yaml`). Details: README, "Building the executables".

> **IMPORTANT — HYCOM utilities.** `bin/` gets two builds of each of
> `gen_hot_from_hycom_0`, `gen_3Dth_from_hycom` and `gen_nudge_from_hycom`:
> `_noscaling` and `_noscaling_lonshift`. Both expect unpacked float data (our
> HYCOM files are unpacked at download with `ncpdq -U`); the stock SCHISM
> utilities apply `scale_factor * 1e-3 + 20` and give wrong values. The
> utilities do **not** convert longitudes, so mesh and HYCOM must share a
> convention: before submitting `gen_hotstart` / `gen_3Dth` / `gen_nudge` the
> workflow compares `fix/hgrid.ll` with the HYCOM `xlon` (this project:
> both 0-360 -> plain executables) and stops if no mesh node is inside the
> HYCOM grid. Without that, every node falls "outside" the HYCOM grid and the
> utilities silently write the constants of `gen_*_from_nc.in` (this happened
> in the first M11 to M14 runs: open-boundary T=10, S=0, u=v=elev=0).

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
# expect: bathymetry.jpg  albedo.jpg  diffmin.jpg  diffmax.jpg
#         watertype.jpg   shapiro.jpg  windrot_geo2proj.jpg
#         bottom_friction.jpg  TEM_nudge.jpg  SAL_nudge.jpg
#         mesh_resolution.jpg  vertical_layers.jpg
#         (estuary.jpg once gen_estuary has run)
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

HYCOM debug GIFs — set only `plot_hycom: true`; sflux GIFs — set only
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

Before submitting, the console prints the longitude check, e.g.
`Longitude check (gen_hotstart): mesh 157.00..220.00, HYCOM 150.00..230.00`
`mesh nodes inside HYCOM: unshifted 100.0 %, shifted 0.0 %  -> ... no shift`.
The job writes `gen_hotstart.done` only if `fort.11` is empty and
`hotstart.nc` exists.

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

`gen_3Dth.done` is written only if `fort.11` is empty and the four files have
time records (`ncdump -h` shows `time = UNLIMITED ; // (N currently)` with
N > 0). `fort.20` holds progress lines, not errors.

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

### Step 3H — plot_hycom_outputs (SLURM array, verification + figures)

Set only `plot_hycom_outputs: true`. It runs after `gen_hotstart`, `gen_3Dth`
and `gen_nudge` (when they are submitted in the same invocation it waits for
them with `afterany`) and checks every group:

```bash
conda activate swf_main
stofs-ak --run --config $CFG
cat $SWF_PROJ/M01/D01/D01_202410/plot_hycom_outputs.txt     # PASS / FAIL + warnings
ls $SWF_PROJ/M01/D01/D01_202410/HYCOM_outputs_*.jpg         # boundary vs HYCOM, nudging, hotstart
```

`FAIL` (no sentinel; `--phase all` stops) means a boundary or nudging file is
missing, empty or **constant** (e.g. `TEM_3D.th.nc: constant (10)`: HYCOM
was not used). `WARN` lines report values outside the expected range or a
large mean difference from the nearest HYCOM value at the open boundary (day
0; validated runs give about 0.03 degC, 0.02 psu, 0.002 m). Open the boundary
figure once: surface T, S, SSH and speed along the open boundary, SCHISM file
vs HYCOM.

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
  (built in step 3b; `--check-fix` also warns if `fix/run_test` runs a
  different executable than `executables.schism`)
- The `executables:` block of `$CFG/project.yaml` (already in the template)
  names them; it must agree with `build.yaml`:
  ```yaml
  executables:
    gen_hotstart: gen_hot_from_hycom_0_noscaling.exe
    gen_3Dth:     gen_3Dth_from_hycom_noscaling.exe
    gen_nudge:    gen_nudge_from_hycom_noscaling.exe
    schism:           pschism_HERCULES_NO_PARMETIS_PREC_EVAP_BLD_STANDALONE_SH_MEM_COMM_TVD-VL
    combine_hotstart: combine_hotstart7.exe
  ```
- `chain_hotstart: true` (in `schism.yaml`, already present in the template
  set copied in section 1) enables automatic chaining.

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
# expect: staout_1 .. staout_20  flux.out  combine_hotstart7.exe

# Month 2 should NOT have hotstart.nc yet (chained at run time):
ls -la $SWF_PROJ/M01/R01/R01_202601/hotstart.nc  # should say "No such file"
```

Re-run a month: `rm $SWF_PROJ/M01/R01/R01_YYYYMM/setup_run.done`

---

### Step 9b — submit_run (blocking — run inside screen/tmux)

Calls `auto_hotstart.py` for the first pending month. That script:
1. Submits `run_test` via sbatch (the SCHISM MPI job)
2. Polls squeue (60 s for the first 5 polls, then 5 / 20 / 30 min, then every
   60 min); checks `outputs/mirror.out` for TIME STEP advancement and cancels a
   hung job. A failed `squeue` call is retried and never treated as "job
   finished". After the job leaves the queue, a SCHISM/MPI error signature
   (`fatal.error`, `err2.out`, `ABORT`) stops the chain with the details; any
   other failure (node failure, OOM, ...) is resubmitted up to 5 times
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

All Phase 5 behavior is configured in `postprocess.yaml` (already copied with
the template set in section 1).

Edit it to choose variables, layers, color scales, temporal cadence, the SST
matching mode, and frame retention. All plots overlay the 200 m and 2000 m
isobaths.

### Step 10a — diag_run_plots (per-stack diagnostics DURING the run)

> Only available for `model_type: schism`. SCHISM+WWM and the UFS-based
> models do not support in-run diagnostics; the flag is ignored with an
> `[N/A]` line.

Enable **before** `setup_run` so the hook is baked into each run directory's
`auto_hotstart.py`. (After changing it later, delete
`R01/R01_*/setup_run.done` and re-run `setup_run`.) As each SCHISM output stack (`out2d_N.nc` + its 3D
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

### Step 10e — download_coops (DTN, CO-OPS station observations)

Parses `fix/station.in`, downloads NOAA CO-OPS observations for every valid
CO-OPS station (water level, water temperature, air pressure, wind), one CSV
per station/product/month into `M01/obs/coops/`. Resume-safe (existing months
skipped). CO-OPS 6-minute data is capped at one month per request.

```bash
ssh hercules-dtn.hpc.msstate.edu
conda activate swf_main
# In steps.yaml: download_coops: true
stofs-ak --run --phase postprocess --only download_coops --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/download_coops_$(date +%Y%m%d_%H%M%S).log
# Verify:
ls $SWF_PROJ/M01/obs/coops/ | head
# e.g. 9459450_water_level_202509.csv  9459450_water_temperature_202509.csv
```

> **station.in convention.** Only lines whose comment is exactly
> `![VARS],<id>,<SOURCE>,<name>` are used, e.g.
> `1 199.496 55.332 0.0 ![WL,T],9459450,CO-OPS,Sand Point`. Lines without a
> bracket (`! modulation1`) or missing the id (`![CU],CO-OPS,UNI1901`) are
> ignored. Datum for water level is set by `coops_datum` in postprocess.yaml
> (default MSL). Restrict variables with `coops_variables`.

### Step 10e.2 — download_ndbc (DTN, NDBC buoy observations)

Parses `fix/station.in`, downloads NOAA NDBC standard-met observations for
every valid NDBC station, one CSV per station/year into `M01/obs/ndbc/`. Past
years use the annual historical file; the current year is assembled from the
completed-month files plus the realtime 45-day file (so there are no gaps).
Resume-safe for past years (immutable); the current year is always refreshed.

```bash
ssh hercules-dtn.hpc.msstate.edu
conda activate swf_main
# In steps.yaml: download_ndbc: true
stofs-ak --run --phase postprocess --only download_ndbc --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/download_ndbc_$(date +%Y%m%d_%H%M%S).log
# Verify:
ls $SWF_PROJ/M01/obs/ndbc/     # e.g. 46035_2025.csv
```

> **NDBC variable mapping.** NDBC stdmet columns map to the model as
> WTMP→T (staout_5), PRES→air_pressure (staout_2), WSPD/WDIR→wind (staout_3/4,
> via u/v). NDBC has no water level. Only the variables in each station's VARS
> bracket are assessed by `station_skill`.

### Step 10f — station_skill (interactive, obs vs model + skill CSV)

Requires `download_coops` and/or `download_ndbc` to have run. Reads
`obs/coops/` + `obs/ndbc/` and the model `R01_YYYYMM/outputs/staout_*`,
produces obs-vs-model time-series plots (with bias/RMSE/R² in the legend) and a
`skill_metrics.csv`, into `P01/P01_station_skill/`. Only the variables named in
each station's VARS bracket are assessed.

```bash
conda activate swf_plot   # (or swf_main; matplotlib is in swf_plot)
# In steps.yaml: station_skill: true
stofs-ak --run --phase postprocess --only station_skill --config $CFG
ls $SWF_PROJ/M01/P01/P01_station_skill/
cat $SWF_PROJ/M01/P01/P01_station_skill/skill_metrics.csv
```

Variables are chosen from each station's `![VARS]` bracket: `WL`/`ELEV`,
`T`, `S` (salinity; CO-OPS stations with a salinity sensor only), `air_pressure`
and `wind`. The model column is selected by the station's line index in
`station.in`, so stations listed at several depths are handled correctly.

> **Re-running for a sub-period.** Set `station_skill_start` /
> `station_skill_end` in `postprocess.yaml` (format YYYY-MM-DD; null = whole
> run) and re-run `station_skill`. No re-download is needed — it just re-slices
> the already-downloaded observations and model output. `station_skill_resample`
> (default `1H`) sets the cadence for the skill metrics.

---

## 11. Phase 5 extras — Argo, waves and altimetry

These steps are configured in `postprocess.yaml` (keys documented inline).

```bash
# Argo float profiles vs model T/S (all models)
stofs-ak --run --phase postprocess --only download_argo  --config $CFG   # DTN
stofs-ak --run --phase postprocess --only collocate_argo --config $CFG   # SLURM array + merge (+ plots if plot_argo: true)
stofs-ak --run --phase postprocess --only plot_argo      --config $CFG   # location map, skill histograms, profile matrix

# Waves (SCHISM+WWM and UFS-SCHISM+WW3 only)
stofs-ak --run --phase postprocess --only download_ndbc        --config $CFG   # DTN (needs [HS,TM01,DM] tokens in station.in)
stofs-ak --run --phase postprocess --only wave_skill           --config $CFG   # NDBC Hs/period/direction vs model stations
stofs-ak --run --phase postprocess --only download_altimetry   --config $CFG   # DTN (CCI needs CCI_FTP_USER / CCI_FTP_PASS)
stofs-ak --run --phase postprocess --only collocate_altimetry  --config $CFG   # SLURM array + merge
stofs-ak --run --phase postprocess --only plot_altimetry       --config $CFG
```

Wave direction (`DM`) metrics use circular statistics (wrapped differences,
circular mean).

---

## 12. SCHISM + WWM (`model_type: schism_wwm`)

Differences from standalone SCHISM:

```bash
python $WF/orchestrator.py --new-project --model schism_wwm --id 01 --dir $SWF_PROJ   # reference run M13
```

This set already includes the self-contained `schism_wwm.yaml` (SCHISM keys
+ `wwm_*`); do not keep a separate `schism.yaml` (it would be ignored).
`build.yaml` builds the SCHISM executable with `USE_WWM: true`.

- `fix/wwminput.nml` is a starter file (from M13). `hgrid_WWM.gr3` (the grid WWM reads) is the SCHISM mesh: `gen_wwmbnd` links it to `hgrid.ll` when it is not in `fix/`.
- `schism_wwm.yaml` holds only the WWM keys. SCHISM keys (`estuary_temp`,
  `tidal_constituents`, ...) have code defaults; copy any you want to change
  from `schism.yaml` into `schism_wwm.yaml`.
- In `project.yaml`: `model_type: schism_wwm` and
  `executables.schism_wwm: <WWM-enabled SCHISM executable>`.
- Extra preprocessing steps (after `gen_param`):

```bash
# steps.yaml: gen_wwmbnd: true  (once)  then  gen_wwminput: true
stofs-ak --run --only gen_wwmbnd   --config $CFG   # fix/wwmbnd.gr3 from hgrid.gr3
stofs-ak --run --only gen_wwminput --config $CFG   # I01_<g>/wwminput.nml per group
```

`BEGTC` / `ENDTC` in each `wwminput.nml` come from that group's
`param.nml` (`start_*` and `start + rnday`), so WWM always covers the same
window as SCHISM. The `&STATION` block is generated from `fix/station.in`
(surface entries), not copied from the template.

- `setup_run` / `submit_run` are used as in section 9; the WWM hotfile
  (`hotfile_out_WWM.nc`) is chained to the next group.
- `diag_run_plots` is not supported for `schism_wwm` (ignored with `[N/A]`).

---

## 13. UFS-SCHISM (`model_type: ufs_schism`)

DATM forcing + CMEPS mediator + SCHISM (old I/O: `schout_*.nc`).

```bash
python $WF/orchestrator.py --new-project --model ufs_schism --id 01 --dir $SWF_PROJ   # reference run M12
```

- `fix/` additionally holds the starter files `datm_in`, `datm.streams`,
  `fd_ufs.yaml` (the one shipped with the built UFS-Coastal version),
  `noahmptable.tbl`, `model_configure`; the build phase puts the UFS
  executable (`executables.ufs_schism` = `fv3_datm2sch.exe`),
  `modules.fv3_datm2sch.lua`, `combine_output11_MPI` and
  `combine_hotstart7.exe` into `bin/`. `run_test` loads that modulefile.
- Preprocessing order after the SCHISM steps of section 6-8
  (`aggregate_hycom`, `gen_sflux`, `gen_estuary`, `gen_bctides`, `gen_source`,
  `gen_param`, `gen_hotstart`, `gen_3Dth`, `gen_nudge`):

```bash
stofs-ak --run --only gen_datm            --config $CFG   # SLURM: sflux -> forcing/datm_<g>.nc
stofs-ak --run --only gen_esmf_mesh       --config $CFG   # SLURM: ESMF mesh of the DATM grid
stofs-ak --run --only plot_datm           --config $CFG   # optional debug GIFs
# interactive (need the DATM output above):
for s in gen_datm_in gen_datm_streams copy_fd_ufs copy_noahmptable \
         gen_model_configure copy_modulefiles gen_ufs_configure; do
  stofs-ak --run --only $s --config $CFG
done
```

  When several of these are enabled in one `stofs-ak --run`, the driver waits
  for `gen_datm` + `gen_esmf_mesh` before running the interactive ones.
- `setup_run` additionally links `forcing/` and `modulefiles/`, creates
  `RESTART/`, and renders `run_combine_output.sbatch`. Check:

```bash
ls $SWF_PROJ/M01/R01/R01_202512/
# expect also: forcing/  modulefiles/  RESTART/  run_combine_output.sbatch
#              datm_in  datm.streams  fd_ufs.yaml  model_configure  ufs.configure
```

- During `submit_run`, at the end of each group `auto_hotstart.py` runs
  `combine_output11_MPI` over all output stacks (`run_combine_output.sbatch`),
  combines the hotstart, and chains SCHISM hotstart + DATM restart
  (`ufs.cpld.datm.r.*.nc`, `rpointer.atm`) + mediator restart
  (`RESTART/ufs.cpld.cpl.r.*.nc`, `rpointer.cpl`) into the next group.
- Output is old I/O, so postprocessing (`plot_outputs`, `compare_sst`,
  `collocate_argo`) reads the combined `schout_*.nc` files automatically.
- **No in-run diagnostics**: `diag_run_plots` is not supported (partition
  files are only combined at the end of each group).
- `chain_hotstart: false` is for single-group tests only: groups after the
  first have no `hotstart.nc` link and cannot start.

---

## 14. UFS-SCHISM + WW3 (`model_type: ufs_schism_ww3`)

Same as section 13 plus WW3:

```bash
python $WF/orchestrator.py --new-project --model ufs_schism_ww3 --id 01 --dir $SWF_PROJ   # reference run M14
```

- `ufs_schism_ww3.yaml` is **self-contained** (it does not inherit from
  `ufs_schism.yaml`). Update `*_petlist_bounds` to match your `fix/run_test`
  allocation before `setup_run`.
- `project.yaml`: `executables.ufs_schism_ww3`, optionally `executables.ww3_ounp`.
  The build target `ufs_datm2sch2ww3` (about an hour of compilation, so run
  the build phase alone first) installs `fv3_datm2sch2ww3.exe`,
  `modules.fv3_datm2sch2ww3.lua` and `ww3_ounp` (from the UFS build) into `bin/`.
- `fix/` additionally: the starter files `ww3_shel.nml`, `ww3_ounp.inp`,
  `namelists.nml`, `ww3_grid.nml`, plus `station.in` (yours; `ww3_points.list` is made from it by `gen_ww3_points` unless you provide one) and the
  mesh-specific files listed in `ww3_fix_files`. `mod_def.ww3`,
  `ST4TABUHF2.bin`, `ww3_ESMFmesh.nc` and `mesh.msh` are made by the step
  `gen_ww3_mesh` when they are not in `fix/` (it needs `fix/hgrid.ll`, the
  build's `ww3_grid` in `bin/`, and runs one SLURM job):

```bash
stofs-ak --run --only gen_ww3_mesh --config $CFG
```

  `mesh.msh` is written with the hgrid's node and element numbering. WW3
  numbers its nodes in file order and the UFS cap takes element *i* of
  `ww3_ESMFmesh.nc` as WW3 node *i*, so the three files must keep the SCHISM
  order; the step refuses a mesh with nodes that no triangle uses and checks
  the ESMF mesh against `mesh.msh`. Files that already exist are kept.
- Extra preprocessing steps (after `gen_param`):

```bash
stofs-ak --run --only gen_ww3_shel --config $CFG   # ww3_shel.nml per group (dates, cadences, restart)
stofs-ak --run --only gen_ww3_ounp --config $CFG   # ww3_ounp.inp per group
```

- `chain_ww3_restart` (default `true`): copy the WW3 restart
  (`ufs.cpld.ww3.r.*.nc`) into the next group. `false` cold-starts WW3 each
  group; DATM and mediator restarts are still chained. If your config was
  copied from an older template with `chain_ww3_restart: false`, WW3 keeps
  cold-starting until you set it to `true`.
- After each group `auto_hotstart.py` also converts the WW3 point output
  (`ww3_ounp`) to `ww3.<date>_tab.nc` for `wave_skill`.
- Postprocess extras: `plot_outputs_ww3` (field GIFs from
  `*.out_grd.ww3.nc`), `wave_skill`, `download_altimetry`,
  `collocate_altimetry`, `plot_altimetry` (section 11).

---

## 15. Sub-monthly grouping (`ndays`)

```yaml
# project.yaml
grouping: weekly        # aliases: daily (1), weekly (7), biweekly (14)
# or
grouping: ndays
group_ndays: 10
```

Group IDs become `YYYYMMDD` (the group start date): `I01_20250901/`,
`R01_20250901/`, ... The last group is shorter if `end_date` does not fall on
a boundary. Each forcing stack gets 3 extra read-ahead days. Everything else
(hotstart chaining, postprocessing windows) works unchanged. With monthly
grouping keep `start_date` on the 1st of a month; a warning is printed
otherwise.

---

## Single-step override

To run one step regardless of the `steps.yaml` flags:

```bash
stofs-ak --run --only download_hycom --config $CFG
```

---

## Running all phases in one command

To run preprocess → run → postprocess in sequence (with a SLURM barrier between
preprocess and run, and a 15-second filesystem-propagation pause before
`setup_run`):

```bash
screen -S stofs_all
conda activate swf_main
stofs-ak --run --phase all --config $CFG \
    2>&1 | tee $SWF_PROJ/M01/logs/run_all_$(date +%Y%m%d_%H%M%S).log
# Ctrl-A D to detach
```

---

## Re-running from scratch (--refresh)

`--refresh` deletes all `*.done` sentinel files across the project so the
next `--run --phase all` re-runs every step from scratch. Raw downloaded data
and generated NetCDF outputs are **not** deleted — steps with output-presence
checks will skip regenerating files that already exist.

```bash
stofs-ak --refresh --config $CFG
```

Output example:
```
============================================================
  --refresh: sentinel reset for M01
  4 month(s): 202509 -> 202512
============================================================

  Deleted 28 sentinel(s):
    I01/I01_202509/gen_nudge.done
    I01/I01_202509/gen_3Dth.done
    ...
    R01/R01_202509/setup_run.done
    R01/R01_202509/run.done
    D01/D01_202509/plot_hycom.done
    ...

  Raw data and NetCDF outputs were NOT deleted.
  Re-run:  stofs-ak --run --phase all --config <cfg>
============================================================
```

Then re-run everything:
```bash
stofs-ak --run --phase all --config $CFG
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
| `plot_hycom` / `plot_sflux` (submit) | 2 | login | swf_main | no |
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
| `download_coops` | 5 | DTN | swf_main | yes |
| `download_ndbc` | 5 | DTN | swf_main | yes |
| `station_skill` | 5 | login/any | swf_plot | no |
| `download_argo` | 5 | DTN | swf_main | yes |
| `collocate_argo` / `plot_argo` | 5 | compute (SLURM) | swf_plot (auto) | no |
| `wave_skill` (WWM / WW3) | 5 | login/any | swf_main | no |
| `download_altimetry` (WWM / WW3) | 5 | DTN | swf_main | yes |
| `collocate_altimetry` (WWM / WW3) | 5 | compute (SLURM) | swf_plot (auto) | no |

---

## Resuming / re-running steps

| Step | Resume behavior |
|------|----------------|
| `inspect_mesh` | Skips if `D01_fix/inspect_mesh.done` exists |
| `download_hycom` | Skips valid daily files; stops on stale month |
| `download_era5` | Skips existing monthly files; stops on stale month |
| `download_glofas` | Skips existing annual files; stops on stale year |
| `aggregate_hycom` | Skips months where `SSH_1/TS_1/UV_1.nc` exist. Missing days inside a month are interpolated (<= 3 in a row); a longer gap fails that stack |
| `gen_sflux` | Skips months where `sflux/gen_sflux.done` exists |
| `plot_hycom` | Skips months where `D01_YYYYMM/plot_hycom.done` exists |
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
| `plot_outputs` | Skips entirely if `P01_plot_outputs/plot_outputs.done` exists. If `P01_plot_outputs/.frames_done` exists (frames done, no GIF), submits GIF assembly only. Otherwise re-renders all frames + GIF. |
| `compare_sst` | Skips entirely if `P01_compare_sst/compare_sst.done` exists. If `P01_compare_sst/.frames_done` exists, submits GIF assembly only. Otherwise re-renders all frames (one array task per day, throttled %50) + GIF. |
| `download_coops` | Skips months where `obs/coops/{id}_{product}_{YYYYMM}.csv` exists |
| `download_ndbc` | Skips past-year files (immutable); always refreshes the current year |
| `station_skill` | Skips if `P01_station_skill/station_skill.done` exists. Re-run by deleting that file (or `--refresh`); re-slices obs+model to station_skill_start/end, overwrites plots + skill_metrics.csv |
| `wave_skill` | Skips if `P01_station_skill/wave_skill.done` exists (delete it or `--refresh` to re-run) |
| `collocate_argo` | Skips if `P01_collocate_argo/collocate_argo.done` exists; `plot_argo.done` guards the plots |
| `collocate_altimetry` | Skips if `P01_collocate_altimetry/collocate_altimetry.done` exists |
| `gen_wwminput` / `gen_ww3_shel` / `gen_ww3_ounp` | Skip groups where the matching `*.done` exists in `I01_<group>/` |

---

## HYCOM data quality

After each month is downloaded, the workflow automatically scans every daily
file for spatially-constant fields (failed HYCOM forecast cycles where a depth
level is filled with a uniform value):

- **1–3 consecutive bad days**: the corrupt file is renamed to
  `ts_YYYYMMDD.nc.bad` and replaced with a linearly interpolated file. A
  `WARNING:` line is printed and the repair is logged. The `repaired_by_workflow`
  global attribute in the repaired file records which neighbors were used.
- **More than 3 consecutive bad days**: the workflow halts with an `ERROR:`
  listing the affected days.

To inspect a repaired file:
```bash
ncdump -h $SWF_PROJ/M01/raw/hycom/ts/ts_20250917.nc | grep repaired
# repaired_by_workflow = "interpolated from ts_20250916.nc (weight 0.500) ..."
ls $SWF_PROJ/M01/raw/hycom/ts/ts_20250917.nc.bad   # original corrupt file
```



- **GOFS 3.1** (`expt_93.0`) covers up to **2024-09-04** — combined ts3z/uv3z.
- **ESPC-D-V02** covers **2024-09-05 → present** — per-day archive files. The
  annual `t3z/YYYY` aggregations are rolling ~70-day windows and silently
  return stale data for older dates; always use archive files for historical
  ESPC data.
- The stale-data check (first vs last day of each month) catches this
  automatically and halts the download with a clear warning.
