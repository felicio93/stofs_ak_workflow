# Tutorial — Running STOFS-AK on Hercules (felicioc)

This is a copy-paste-ready walkthrough for **my** Hercules setup. Paths and
settings below are hardcoded to my case:

- Repo clone:   `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow`
- Project dir:  `/work2/noaa/nos-surge/felicioc/STOFS_3D_AK`
- Model:        `M01`  (so `M01/`, `I01/`, `R01/`, `P01/`, `D01/`)
- conda base:   `/work2/noaa/nos-surge/felicioc/envs/miniconda3`
- Envs:         `swf_main` (download + aggregate), `swf_plot` (plotting)
- Dates:        `2024-09-01` → `2026-06-30` (monthly)
- Domain:       lon 150–230, lat 45–78, `lon_reference: "360"`

Shortcuts used below:
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
# later, to update:
cd stofs_ak_workflow && git pull
```

---

## 1. One-time: create the config

The `--setup-envs` and `--init` commands both read the config, so create it
first.

```bash
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config

mkdir -p $CFG
cp $WF/templates/config_example/*.yaml $CFG/
```

The template `project.yaml`, `domain.yaml`, and `envs.yaml` are already filled
with my values. Confirm they look right:
```bash
cat $CFG/project.yaml    # project_dir=/work2/.../STOFS_3D_AK, dates, slurm block
cat $CFG/domain.yaml     # lon 150-230, lat 45-78, lon_reference "360", plot ranges
cat $CFG/envs.yaml       # conda_base, swf_main / swf_plot
```

---

## 2. One-time: create the conda environments (on the DTN)

Env creation needs internet, so do it on the DTN. The `--setup-envs` command
creates `swf_main` and `swf_plot` if missing, or verifies their libraries if
they already exist.

Note the bootstrap: you need a python with `pyyaml` just to run the
orchestrator. So the very first time, create `swf_main` manually, then let
`--setup-envs` create `swf_plot` and verify `swf_main`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config

# First time only: create swf_main manually so the orchestrator can run.
conda create -y -n swf_main -c conda-forge python=3.11 pyyaml python-dateutil nco cdo
conda activate swf_main

# Now create/verify all envs referenced by the config (creates swf_plot).
python $WF/orchestrator.py --setup-envs --config $CFG
```

This ensures both envs exist and reports any missing packages.

---

## 3. One-time: initialize the directory tree

```bash
conda activate swf_main
python $WF/orchestrator.py --init --config $CFG
```

Creates under `$SWF_PROJ/M01/`: `fix/ bin/ raw/hycom/{ssh,ts,uv} raw/era5
I01/I01_YYYYMM R01/... P01/... D01/D01_YYYYMM D01/logs`.

Verify:
```bash
ls $SWF_PROJ/M01
ls $SWF_PROJ/M01/I01 | head
```

Then copy your fixed files and executables:
```bash
# cp /path/to/hgrid.gr3 vgrid.in ... $SWF_PROJ/M01/fix/
# cp /path/to/compiled/gen_*_from_hycom.exe $SWF_PROJ/M01/bin/
```

---

## 4. Step 1 — Download HYCOM (DTN only)

Edit `$CFG/steps.yaml`:
```yaml
download_hycom: true
aggregate_hycom: false
plotting_debug: false
```

Run on the DTN (internet required). For a quick smoke test, temporarily set
`end_date: "2024-09-03"` in `project.yaml`.

```bash
ssh hercules-dtn.hpc.msstate.edu
export WF=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/stofs_ak_workflow
export CFG=/work2/noaa/nos-surge/felicioc/STOFS_3D_AK/M01/config
conda activate swf_main

python $WF/orchestrator.py --run --config $CFG 2>&1 | tee $SWF_PROJ/M01/logs/hycom_download.log
```

Expect: DTN check OK, env check OK, `OK` per SSH/TS/UV per day, and a
**stale-data check** at the end (must say "passed" — if it warns about
identical first/last day, the epoch mapping needs attention).

For the full run, restore the dates and run in the background:
```bash
nohup python $WF/orchestrator.py --run --config $CFG > $SWF_PROJ/M01/logs/hycom_full.log 2>&1 &
tail -f $SWF_PROJ/M01/logs/hycom_full.log
```

Verify a file:
```bash
ncdump -h $SWF_PROJ/M01/raw/hycom/ts/ts_20240901.nc | grep -E "water_temp|salinity|depth|time ="
```

> HYCOM server note: `expt_93.0` ends **2024-09-04**; dates on/after
> **2024-09-05** come from **ESPC-D-V02** (separate t3z/s3z/u3z/v3z files,
> merged automatically). The workflow selects the right source per date.

---

## 5. Step 2 — Aggregate into monthly stacks (any node, interactive)

Edit `$CFG/steps.yaml`:
```yaml
download_hycom: false
aggregate_hycom: true
plotting_debug: false
```

```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG 2>&1 | tee $SWF_PROJ/M01/logs/hycom_aggregate.log
```

Writes `SSH_1.nc`, `TS_1.nc`, `UV_1.nc` into each `I01_YYYYMM/`. TS is
converted to potential temperature and its variable renamed to `temperature`.

**Critical check** — the SCHISM Fortran needs a variable literally named
`temperature`:
```bash
ncdump -h $SWF_PROJ/M01/I01/I01_202409/TS_1.nc | grep -E "temperature|salinity|time ="
```

---

## 6. Step 3 — Debug plots (SLURM job array)

Edit `$CFG/steps.yaml`:
```yaml
download_hycom: false
aggregate_hycom: false
plotting_debug: true
```

Submit from a login node (has `sbatch`):
```bash
conda activate swf_main
python $WF/orchestrator.py --run --config $CFG
```

This submits one array task per month using `swf_plot`. SLURM settings come
from the `slurm:` block in `project.yaml` (account `nos-surge`, partition
`hercules-2`).

Monitor and inspect:
```bash
squeue -u $USER
cat $SWF_PROJ/M01/D01/logs/plot_1.out
ls -lh $SWF_PROJ/M01/D01/D01_202409/
# HYCOM_temperature_202409.gif  HYCOM_salinity_202409.gif  HYCOM_ssh_202409.gif
```

Plot color ranges (from `domain.yaml`): temp -2/16, salinity 31.5/34,
ssh -1/1.

---

## Quick reference — which env, which node

| Step | Node | Env | Internet |
|------|------|-----|----------|
| `--setup-envs` | DTN | any (bootstrap) | yes |
| `--init` | any | swf_main | no |
| `download_hycom` | DTN | swf_main | yes |
| `aggregate_hycom` | login/any | swf_main | no |
| `plotting_debug` (submit) | login | swf_main | no |
| plotting (compute jobs) | compute | swf_plot (auto) | no |

## Resuming / reruns
- Download and aggregate are resume-safe: valid existing files are skipped,
  incomplete ones re-created. Just re-run the same command.
- Plotting: re-submit; re-run overwrites GIFs. Re-submit a single month by
  editing the array range in `$SWF_PROJ/M01/D01/plot_hycom.sbatch` if needed.
