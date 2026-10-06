#!/bin/bash --login
# =============================================================================
# SLURM MPI job for plot_outputs_ww3 Stage 1 (parallel WW3 frame generation).
# Each MPI rank processes one *.out_grd.ww3.nc field file independently.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -q {{QOS}}
#SBATCH -N {{NNODES}}
#SBATCH -n {{NTASKS}}
#SBATCH --mem-per-cpu={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_outputs_ww3_mpi.out
#SBATCH -e {{LOGDIR}}/plot_outputs_ww3_mpi.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

module load intel-oneapi-compilers/2022.2.1
module load intel-oneapi-mpi/2021.7.1

echo "=== plot_outputs_ww3 MPI frames: $(date) ==="
echo "  Nodes: ${SLURM_JOB_NUM_NODES}  Ranks: ${SLURM_NTASKS}"

srun --mpi=pmi2 {{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} mpi-frames

echo "=== plot_outputs_ww3 MPI frames complete: $(date) ==="
