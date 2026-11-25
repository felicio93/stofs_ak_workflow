#!/bin/bash --login
# =============================================================================
# SLURM job array template for aggregate_hycom (daily HYCOM -> SSH_1, UV_1,
# TS_1 stacks). One task per group and variable, each on its own node.
# Rendered/submitted by workflow.models.schism.preprocess.aggregate_hycom.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NTASKS}}
#SBATCH -N 1
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/aggregate_hycom_%a.out
#SBATCH -e {{LOGDIR}}/aggregate_hycom_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# NCO and CDO of the conda env (the job does not activate it)
export PATH={{ENVBIN}}:${PATH}

read GROUP VAR <<< "$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})"
echo "=== aggregate_hycom: task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP} ${VAR}  $(hostname)  $(date) ==="
{{PY}} -m workflow.models.schism.preprocess.aggregate_hycom --config {{CONFIG_DIR}} --group "${GROUP}" --var "${VAR}"
echo "=== aggregate_hycom complete: ${GROUP} ${VAR}  $(date) ==="
