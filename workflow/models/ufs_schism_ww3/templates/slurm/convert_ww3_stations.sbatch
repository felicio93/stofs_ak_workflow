#!/bin/bash --login
# =============================================================================
# SLURM template for convert_ww3_stations (ww3_ounp point output conversion).
# =============================================================================
#SBATCH -D {{RUNDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -N 1
#SBATCH -n 1
#SBATCH -t 00:10:00
#SBATCH -o {{LOGDIR}}/convert_ww3_sta_{{MONTH}}.out
#SBATCH -e {{LOGDIR}}/convert_ww3_sta_{{MONTH}}.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=FAIL

set -e
ulimit -s unlimited

# Load UFS environment from the fix/ directory modulefile
# (same environment used by the main model run)
module use {{FIXDIR}}
module load modules.fv3_datm2sch

cd {{RUNDIR}}

echo "=== convert_ww3_stations: {{MONTH}} ==="
echo "Run dir: $(pwd)"
echo "out_pnt.ww3 size: $(ls -lh out_pnt.ww3 2>/dev/null || echo 'MISSING')"

if [ ! -f out_pnt.ww3 ]; then
    echo "ERROR: out_pnt.ww3 not found in {{RUNDIR}}"
    exit 1
fi

if [ ! -f mod_def.ww3 ]; then
    echo "ERROR: mod_def.ww3 not found in {{RUNDIR}}"
    exit 1
fi

if [ ! -f ww3_ounp.inp ]; then
    echo "ERROR: ww3_ounp.inp not found in {{RUNDIR}}"
    exit 1
fi

srun -n 1 {{WW3_OUNP_EXE}}

echo "=== ww3_ounp complete ==="
echo "Files produced:"
ls -lh ww3.*.nc 2>/dev/null || echo "WARNING: no ww3.*.nc files found"

touch convert_ww3_sta.done
echo "=== convert_ww3_stations done: {{MONTH}} ==="
