#!/bin/bash --login
# =============================================================================
# SLURM job array template for plotting_debug.
# One array task per month. Rendered/submitted by workflow/submit_plots.py.
# Placeholders in {{ }} are substituted before submission.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NMONTHS}}
#SBATCH -n 1
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_%a.out
#SBATCH -e {{LOGDIR}}/plot_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Map this array index (1-based) to a month string via the generated manifest.
MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "Array task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH}"

# Run the plotting worker using the swf_plot interpreter directly
# (no conda activate needed in a non-interactive shell).
{{PY}} {{PLOT_SCRIPT}} --config {{CONFIG_DIR}} --month "${MONTH}"
