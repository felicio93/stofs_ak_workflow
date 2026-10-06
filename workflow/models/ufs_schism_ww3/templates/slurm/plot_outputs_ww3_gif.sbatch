#!/bin/bash --login
# =============================================================================
# SLURM template for plot_outputs_ww3 Stage 2 (serial GIF assembly).
# Runs once, after all frame array tasks complete (--dependency=afterok).
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_outputs_ww3_gif.out
#SBATCH -e {{LOGDIR}}/plot_outputs_ww3_gif.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== plot_outputs_ww3 GIF assembly ==="
{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} assemble
echo "=== plot_outputs_ww3 GIF assembly complete ==="
