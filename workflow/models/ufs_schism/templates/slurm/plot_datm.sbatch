#!/bin/bash --login
# =============================================================================
# SLURM job array template for plot_datm (DATM forcing debug GIFs).
# One task per group. Works for monthly (YYYYMM) and ndays (YYYYMMDD).
# Rendered/submitted by
# workflow.models.ufs_schism.preprocess.submit_plot_datm.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NGROUPS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_datm_%a.out
#SBATCH -e {{LOGDIR}}/plot_datm_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

GROUP=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== plot_datm: task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --month "${GROUP}"

echo "=== plot_datm complete for group ${GROUP} ==="
