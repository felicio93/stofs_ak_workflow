#!/bin/bash --login
# =============================================================================
# SLURM MPI job for compare_sst Stage 1 (parallel per-day frame generation).
# Each MPI rank processes a subset of the comparison days independently.
# Rendered/submitted by workflow.models.schism.postprocess.submit_compare_sst.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -N {{NNODES}}
#SBATCH -n {{NTASKS}}
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/compare_sst_mpi.out
#SBATCH -e {{LOGDIR}}/compare_sst_mpi.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== compare_sst MPI frames: $(date) ==="
echo "  Nodes: ${SLURM_JOB_NUM_NODES}  Ranks: ${SLURM_NTASKS}"

srun --mpi=pmix {{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} mpi-frames

echo "=== compare_sst MPI frames complete: $(date) ==="
