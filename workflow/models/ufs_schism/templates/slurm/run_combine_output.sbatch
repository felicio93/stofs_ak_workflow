#!/bin/bash --login
# =============================================================================
# SLURM job template for combine_output11_MPI (UFS-SCHISM old I/O).
# Combines per-rank schout_NNNNNN_*.nc files into global schout_*.nc files.
# Submitted by auto_hotstart.py after the run completes successfully.
# Runs inside outputs/ (#SBATCH -D ./outputs).
#
# Environment variables passed via --export at submit time:
#   COMBINE_BEGIN  : first stack index to combine
#   COMBINE_END    : last stack index to combine
#   COMBINE_JOBNAME: SLURM job name (e.g. CO02_01)
# =============================================================================
#SBATCH -D ./outputs
#SBATCH -J {{COMBINE_JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -N {{COMBINE_NODES}}
#SBATCH -n {{COMBINE_NRANKS}}
#SBATCH --exclusive
#SBATCH -t {{COMBINE_WALLTIME}}
#SBATCH -o {{LOGDIR}}/combine_output_%j.out
#SBATCH -e {{LOGDIR}}/combine_output_%j.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=END,FAIL

set -e
ulimit -s unlimited

module use modulefiles
module load modules.fv3_datm2sch

echo "=== combine_output11_MPI: stacks ${COMBINE_BEGIN} to ${COMBINE_END} ==="
echo "Working dir: $(pwd)"
echo "MPI ranks: {{COMBINE_NRANKS}}"

srun ./combine_output11_MPI \
    -b ${COMBINE_BEGIN} \
    -e ${COMBINE_END} \
    -w 0 \
    -o schout

echo "=== combine_output11_MPI complete ==="
