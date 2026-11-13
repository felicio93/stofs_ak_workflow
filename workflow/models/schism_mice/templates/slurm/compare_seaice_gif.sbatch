#!/bin/bash --login
# =============================================================================
# SLURM template for compare_seaice Stage 2 (serial GIF assembly).
# Runs once, after all daily frame tasks complete (--dependency=afterok).
# Rendered/submitted by workflow.models.schism_mice.postprocess.submit_compare_seaice.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/compare_seaice_gif.out
#SBATCH -e {{LOGDIR}}/compare_seaice_gif.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== compare_seaice GIF assembly ==="
{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} assemble
echo "=== compare_seaice GIF assembly complete ==="
