#!/bin/bash --login
# =============================================================================
# SLURM template for collocate_argo Stage 3 (plot_argo diagnostics).
# Runs once, after the merge job completes (--dependency=afterok).
# Generates all four Argo diagnostic plots:
#   argo_location_map.jpg, argo_skill_histograms_{var}.jpg,
#   argo_profiles_{var}.jpg, argo_skill_map_{var}.jpg
# Rendered/submitted by submit_collocate_argo.py
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/argo_plot.out
#SBATCH -e {{LOGDIR}}/argo_plot.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== plot_argo: $(date) ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}}

echo "=== plot_argo complete: $(date) ==="
