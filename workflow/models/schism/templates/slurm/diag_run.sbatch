#!/bin/bash --login
# =============================================================================
# SLURM job array template for diag_run_plots (per-output-stack diagnostic
# frames). Submitted by auto_hotstart.py during the run, once per newly-
# completed output stack. One array task per configured variable so that all
# variables for a stack are plotted in parallel.
#
# At submit time auto_hotstart.py passes DIAG_MONTH and DIAG_STACK via
# --export. The array index selects the variable from DIAG_VARS_MANIFEST
# (a one-var-per-line file written by setup_run into the run directory).
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NDIAG_VARS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/diag_%A_%a.out
#SBATCH -e {{LOGDIR}}/diag_%A_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Map array index -> variable name via the manifest written by setup_run.
VARNAME=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{DIAG_VARS_MANIFEST}})
echo "=== diag_run_plots: ${DIAG_MONTH} stack ${DIAG_STACK} var ${VARNAME} ==="
{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} \
    --month "${DIAG_MONTH}" --stack "${DIAG_STACK}" --var "${VARNAME}"
echo "=== diag_run_plots complete: ${DIAG_MONTH} stack ${DIAG_STACK} var ${VARNAME} ==="
