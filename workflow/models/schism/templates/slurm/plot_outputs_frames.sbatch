#!/bin/bash --login
# =============================================================================
# SLURM array template for plot_outputs Stage 1 (frame generation).
# One array task per output stack file. Rendered/submitted by
# workflow.models.schism.postprocess.submit_plot_outputs.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NTASKS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_outputs_frames_%a.out
#SBATCH -e {{LOGDIR}}/plot_outputs_frames_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Manifest line: "YYYYMM prefix stack"
LINE=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
MONTH=$(echo "${LINE}" | awk '{print $1}')
PREFIX=$(echo "${LINE}" | awk '{print $2}')
STACK=$(echo "${LINE}" | awk '{print $3}')
echo "=== plot_outputs frames: task ${SLURM_ARRAY_TASK_ID} -> ${MONTH} ${PREFIX}_${STACK} ==="

{{PY}} {{SCRIPT}} frames --config {{CONFIG_DIR}} \
    --month "${MONTH}" --prefix "${PREFIX}" --stack "${STACK}"
echo "=== done ${MONTH} ${PREFIX}_${STACK} ==="
