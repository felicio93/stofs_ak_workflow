#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_esmf_mesh (DATM grid -> ESMF mesh).
# One task per group. Works for monthly (YYYYMM) and ndays (YYYYMMDD).
# Rendered/submitted by
# workflow.models.ufs_schism.preprocess.submit_esmf_mesh.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NGROUPS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_esmf_mesh_%a.out
#SBATCH -e {{LOGDIR}}/gen_esmf_mesh_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

GROUP=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_esmf_mesh: task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --group "${GROUP}"

echo "=== gen_esmf_mesh complete for group ${GROUP} ==="
