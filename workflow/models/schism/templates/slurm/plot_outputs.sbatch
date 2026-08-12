#!/bin/bash --login
# =============================================================================
# SLURM job array template for plot_outputs (SCHISM field output GIFs).
# One array task per month. Rendered/submitted by
# workflow.models.schism.postprocess.submit_plot_outputs.
# Placeholders in {{ }} are substituted before submission.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NMONTHS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_outputs_%a.out
#SBATCH -e {{LOGDIR}}/plot_outputs_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== plot_outputs: array task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --month "${MONTH}"
echo "=== plot_outputs complete for month ${MONTH} ==="
