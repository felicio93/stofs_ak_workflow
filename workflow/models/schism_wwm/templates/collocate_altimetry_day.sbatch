#!/bin/bash --login
# =============================================================================
# SLURM array template for collocate_altimetry Stage 1 (per-day collocation).
# One array task per calendar day. Each task collocates satellite altimetry
# Hs against SCHISM+WWM sigWaveHeight for its assigned day.
# Rendered/submitted by submit_collocate_altimetry.py
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NTASKS}}%{{ARRAY_THROTTLE}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/alt_day_%a.out
#SBATCH -e {{LOGDIR}}/alt_day_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

DAY=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== collocate_altimetry day: task ${SLURM_ARRAY_TASK_ID} -> ${DAY} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} day --date "${DAY}"

echo "=== done ${DAY} ==="
