#!/bin/bash --login
# =============================================================================
# SLURM array template for plot_outputs_ww3 Stage 1 (per-file frame rendering).
# One array task per *.out_grd.ww3.nc file. No MPI needed — each task
# renders all variables for its assigned file independently.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -q {{QOS}}
#SBATCH --array=1-{{NTASKS}}%{{ARRAY_THROTTLE}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/ww3frm_%a.out
#SBATCH -e {{LOGDIR}}/ww3frm_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Map array index to file path via manifest
NCFILE=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== plot_outputs_ww3 frames: task ${SLURM_ARRAY_TASK_ID} -> ${NCFILE} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} frames --file "${NCFILE}"

echo "=== done ${NCFILE} ==="
