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
#SBATCH -q {{QOS}}
#SBATCH -N {{NNODES}}
#SBATCH -n {{NTASKS}}
#SBATCH --mem-per-cpu={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/plot_outputs_mpi.out
#SBATCH -e {{LOGDIR}}/plot_outputs_mpi.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Load Intel MPI so mpi4py finds the correct PMI interface (pmi2) at runtime.
# #!/bin/bash --login ensures the module system is initialised without needing
# to source /etc/profile.d/modules.sh explicitly.
module load intel-oneapi-compilers/2022.2.1
module load intel-oneapi-mpi/2021.7.1

echo "=== plot_outputs MPI frames: $(date) ==="
echo "  Nodes: ${SLURM_JOB_NUM_NODES}  Ranks: ${SLURM_NTASKS}"

srun --mpi=pmi2 {{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} mpi-frames

echo "=== plot_outputs MPI frames complete: $(date) ==="
