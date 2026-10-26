#!/bin/bash --login
# =============================================================================
# SLURM job for gen_ww3_mesh: ww3_grid (mesh.msh -> mod_def.ww3 + scrip.nc +
# ST4TABUHF2.bin) and ESMF_Scrip2Unstruct (scrip.nc -> ww3_ESMFmesh.nc).
# Runs in M{ID}/ww3_mesh/ with the environment of the UFS executable, so
# ww3_grid and the ESMF tool match the model. Rendered/submitted by
# workflow.models.ufs_schism_ww3.preprocess.gen_ww3_mesh.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_ww3_mesh.out
#SBATCH -e {{LOGDIR}}/gen_ww3_mesh.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e
ulimit -s unlimited

module use {{BIN_DIR}}
module load {{UFS_MODULE}}

cd {{WORKDIR}}

echo "=== ww3_grid: {{GRID_EXE}} ==="
srun -n 1 {{GRID_EXE}} ww3_grid.nml > ww3_grid.out
for f in mod_def.ww3 ST4TABUHF2.bin scrip.nc; do
  test -s "$f" || { echo "ERROR: ww3_grid did not make $f (see ww3_grid.out)"; exit 1; }
done

{{ESMF_LINES}}
echo "=== ESMF_Scrip2Unstruct scrip.nc -> ww3_ESMFmesh.nc ==="
srun -n 1 "$ESMF_S2U" scrip.nc ww3_ESMFmesh.nc 0
test -s ww3_ESMFmesh.nc || { echo "ERROR: ww3_ESMFmesh.nc not made"; exit 1; }

ls -lh mod_def.ww3 ST4TABUHF2.bin scrip.nc ww3_ESMFmesh.nc
echo "=== gen_ww3_mesh done ==="
