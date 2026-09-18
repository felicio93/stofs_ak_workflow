#!/bin/bash --login
# =============================================================================
# SLURM template for collocate_altimetry Stage 2 (serial merge).
# Runs once, after all daily array tasks complete (--dependency=afterok).
# Concatenates daily NetCDFs -> collocated_hs.nc and writes sentinel.
# Rendered/submitted by submit_collocate_altimetry.py
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/alt_merge.out
#SBATCH -e {{LOGDIR}}/alt_merge.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== collocate_altimetry merge: $(date) ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} merge

echo "=== collocate_altimetry merge complete: $(date) ==="
