#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_esmf_mesh (one task per month).
# Rendered/submitted by workflow.models.ufs_schism.preprocess.submit_esmf_mesh.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NMONTHS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_esmf_mesh_%a.out
#SBATCH -e {{LOGDIR}}/gen_esmf_mesh_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_esmf_mesh: task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --month "${MONTH}"

echo "=== gen_esmf_mesh complete for month ${MONTH} ==="
