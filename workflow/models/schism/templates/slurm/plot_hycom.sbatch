#!/bin/bash --login
# =============================================================================
# SLURM job array template for plot_hycom debug GIFs.
# One array task per group. Works for monthly (YYYYMM) and ndays (YYYYMMDD).
# Rendered/submitted by workflow.diagnostics.submit_plots.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NGROUPS}}
#SBATCH -n 1
#SBATCH --mem={{MEM_PER_TASK}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_hycom_%a.out
#SBATCH -e {{LOGDIR}}/plot_hycom_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Map this array index (1-based) to a group ID via the generated manifest
GROUP=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "Array task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP}"

# Run the plotting worker using the swf_plot interpreter directly
{{PY}} {{PLOT_SCRIPT}} --config {{CONFIG_DIR}} --month "${GROUP}"
