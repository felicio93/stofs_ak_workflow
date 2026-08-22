#!/bin/bash --login
# =============================================================================
# SLURM array template for collocate_argo Stage 1 (per-day collocation).
# One array task per calendar day. Each task collocates ALL configured
# variables (temperature + salinity) for its assigned day, reusing the
# expensive KDTree build across variables.
# Rendered/submitted by submit_collocate_argo.py
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NTASKS}}%{{ARRAY_THROTTLE}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/argo_day_%a.out
#SBATCH -e {{LOGDIR}}/argo_day_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

DAY=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== collocate_argo day: task ${SLURM_ARRAY_TASK_ID} -> ${DAY} ==="

{{PY}} {{SCRIPT}} day --date "${DAY}" --config {{CONFIG_DIR}}

echo "=== done ${DAY} ==="
