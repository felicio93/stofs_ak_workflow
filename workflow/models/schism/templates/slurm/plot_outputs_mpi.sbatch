#!/bin/bash --login
# =============================================================================
# SLURM MPI job for plot_outputs Stage 1 (parallel frame generation).
# Each MPI rank processes a subset of the output stack files independently.
# Rendered/submitted by workflow.models.schism.postprocess.submit_plot_outputs.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -N {{NNODES}}
#SBATCH -n {{NTASKS}}
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_outputs_mpi.out
#SBATCH -e {{LOGDIR}}/plot_outputs_mpi.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== plot_outputs MPI frames: $(date) ==="
echo "  Nodes: ${SLURM_JOB_NUM_NODES}  Ranks: ${SLURM_NTASKS}"

srun --mpi=pmix {{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} mpi-frames

echo "=== plot_outputs MPI frames complete: $(date) ==="
