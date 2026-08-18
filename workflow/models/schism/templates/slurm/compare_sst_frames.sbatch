#!/bin/bash --login
# =============================================================================
# SLURM array template for compare_sst Stage 1 (per-day frames).
# One array task per day. Rendered/submitted by
# workflow.models.schism.postprocess.submit_compare_sst.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NTASKS}}%{{ARRAY_THROTTLE}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/compare_sst_frames_%a.out
#SBATCH -e {{LOGDIR}}/compare_sst_frames_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

DAY=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== compare_sst frames: task ${SLURM_ARRAY_TASK_ID} -> ${DAY} ==="

{{PY}} {{SCRIPT}} frames --config {{CONFIG_DIR}} --date "${DAY}"
echo "=== done ${DAY} ==="
