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

# Load Intel MPI so mpi4py finds the correct PMI interface (pmi2) at runtime.
source /etc/profile.d/modules.sh
module load intel-oneapi-compilers/2022.2.1
module load intel-oneapi-mpi/2021.7.1

echo "=== compare_sst MPI frames: $(date) ==="
echo "  Nodes: ${SLURM_JOB_NUM_NODES}  Ranks: ${SLURM_NTASKS}"

srun --mpi=pmi2 {{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} mpi-frames

echo "=== compare_sst MPI frames complete: $(date) ==="
