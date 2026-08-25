#!/bin/bash --login
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NMONTHS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_datm_%a.out
#SBATCH -e {{LOGDIR}}/gen_datm_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_datm: task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH} ==="

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --month "${MONTH}"

echo "=== gen_datm complete for month ${MONTH} ==="