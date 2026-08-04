#!/bin/bash --login
# =============================================================================
# SLURM job array template for plot_sflux (sflux debug GIFs).
# One task per month. Rendered/submitted by workflow/submit_era5.py.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NMONTHS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_sflux_%a.out
#SBATCH -e {{LOGDIR}}/plot_sflux_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== plot_sflux: array task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --month "${MONTH}"
echo "=== plot_sflux complete for month ${MONTH} ==="
