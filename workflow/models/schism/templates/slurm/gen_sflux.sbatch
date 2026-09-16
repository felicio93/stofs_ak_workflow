#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_sflux (ERA5 raw -> SCHISM sflux).
# One task per group. Works for monthly (YYYYMM) and ndays (YYYYMMDD) IDs.
# Rendered/submitted by workflow.models.schism.preprocess.submit_era5.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NGROUPS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_sflux_%a.out
#SBATCH -e {{LOGDIR}}/gen_sflux_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

GROUP=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_sflux: array task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP} ==="

# Skip if already complete (resume safety)
SENTINEL={{IBASEDIR}}/I{{PID}}_${GROUP}/sflux/gen_sflux.done
if [ -f "${SENTINEL}" ]; then
    echo "Sentinel found -- already complete, exiting."
    exit 0
fi

{{PY}} {{SCRIPT}} --config {{CONFIG_DIR}} --group "${GROUP}"
echo "=== gen_sflux complete for group ${GROUP} ==="
