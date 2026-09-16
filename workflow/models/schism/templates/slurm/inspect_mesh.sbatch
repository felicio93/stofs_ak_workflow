#!/bin/bash --login
# =============================================================================
# SLURM job template for inspect_mesh (single job, diagnostic plots).
# Rendered/submitted by workflow.diagnostics.submit_inspect_mesh.
# Grouping-agnostic: runs once, writes to D{ID}/D{ID}_fix/.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/inspect_mesh.out
#SBATCH -e {{LOGDIR}}/inspect_mesh.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== inspect_mesh: generating fix/ diagnostic plots ==="
echo "Working dir: $(pwd)"

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}}
echo "=== inspect_mesh complete ==="
