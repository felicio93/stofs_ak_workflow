#!/bin/bash --login
# =============================================================================
# SLURM template for diag_run_plots (per-output-stack diagnostic frames).
# Submitted by auto_hotstart.py during the run, once per newly-completed
# output stack. The month and stack number are passed at submit time via
# environment variables DIAG_MONTH and DIAG_STACK (sbatch --export), so this
# rendered script is reused for every stack. Single core, short walltime.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/diag_%A.out
#SBATCH -e {{LOGDIR}}/diag_%A.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

echo "=== diag_run_plots: ${DIAG_MONTH} stack ${DIAG_STACK} ==="
{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --month "${DIAG_MONTH}" --stack "${DIAG_STACK}"
echo "=== diag_run_plots complete: ${DIAG_MONTH} stack ${DIAG_STACK} ==="
