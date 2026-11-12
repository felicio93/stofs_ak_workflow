#!/bin/bash --login
# =============================================================================
# SLURM job template for gen_ice_hotstart (single job, first group only):
# HYCOM sea ice -> sic2/sih2/siu2/siv2 in I{ID}_{first}/hotstart.nc.
# Rendered/submitted by workflow.models.schism_mice.preprocess.gen_ice_hotstart
# (after gen_hotstart when that job was submitted in the same run).
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_ice_hotstart.out
#SBATCH -e {{LOGDIR}}/gen_ice_hotstart.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== gen_ice_hotstart ==="
{{PY}} -m workflow.models.schism_mice.preprocess.gen_ice_hotstart --config {{CONFIG_DIR}}
echo "=== gen_ice_hotstart complete ==="
