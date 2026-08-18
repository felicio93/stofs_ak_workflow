#!/bin/bash --login
# =============================================================================
# SLURM template for compare_sst Stage 2 (serial GIF assembly).
# Runs once, after all daily frame tasks complete (--dependency=afterok).
# Rendered/submitted by workflow.models.schism.postprocess.submit_compare_sst.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/compare_sst_gif.out
#SBATCH -e {{LOGDIR}}/compare_sst_gif.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== compare_sst GIF assembly ==="
{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} assemble
echo "=== compare_sst GIF assembly complete ==="
