#!/bin/bash --login
# =============================================================================
# SLURM template for collocate_argo Stage 2 (serial merge + distance filter).
# Runs once, after all daily array tasks complete (--dependency=afterok).
# Concatenates daily NetCDFs -> collocated_{var}.nc + collocated_{var}_clean.nc
# Rendered/submitted by submit_collocate_argo.py
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/argo_merge.out
#SBATCH -e {{LOGDIR}}/argo_merge.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== collocate_argo merge: $(date) ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} merge

echo "=== collocate_argo merge complete: $(date) ==="
