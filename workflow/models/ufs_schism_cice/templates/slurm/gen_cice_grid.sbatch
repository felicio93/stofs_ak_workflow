#!/bin/bash --login
# =============================================================================
# SLURM job template for gen_cice_grid (single job, --phase setup):
# fix/hgrid.ll -> fix/cice/{grid,kmt,bathy,mesh}_cice.nc.
# Rendered/submitted by workflow.models.ufs_schism_cice.preprocess.gen_cice_grid
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_cice_grid.out
#SBATCH -e {{LOGDIR}}/gen_cice_grid.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== gen_cice_grid ==="
{{PY}} -m workflow.models.ufs_schism_cice.preprocess.gen_cice_grid --config {{CONFIG_DIR}}
echo "=== gen_cice_grid complete ==="
