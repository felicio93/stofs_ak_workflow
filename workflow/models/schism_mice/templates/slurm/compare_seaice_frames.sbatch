#!/bin/bash --login
# =============================================================================
# SLURM array template for compare_seaice Stage 1 (per-day frames).
# One array task per manifest line (one day, or several consecutive days for
# long runs). Rendered/submitted by
# workflow.models.schism_mice.postprocess.submit_compare_seaice.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NTASKS}}%{{ARRAY_THROTTLE}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/compare_seaice_frames_%a.out
#SBATCH -e {{LOGDIR}}/compare_seaice_frames_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

DAYS=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== compare_seaice frames: task ${SLURM_ARRAY_TASK_ID} -> ${DAYS} ==="

for DAY in ${DAYS}; do
    {{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} frames --date "${DAY}"
    echo "=== done ${DAY} ==="
done
