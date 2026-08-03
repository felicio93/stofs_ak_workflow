#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_3Dth (one task per month).
# Rendered/submitted by workflow/submit_schism.py.
# Placeholders in {{ }} are substituted before submission.
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NMONTHS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_3Dth_%a.out
#SBATCH -e {{LOGDIR}}/gen_3Dth_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Map array index to month string via manifest.
MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_3Dth: array task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH} ==="

IDIR={{IBASEDIR}}/I{{PID}}_${MONTH}
cd "${IDIR}" || { echo "ERROR: cannot cd to ${IDIR}"; exit 1; }
echo "Working dir: $(pwd)"

# Skip if already completed.
if [ -f "gen_3Dth.done" ]; then
    echo "gen_3Dth.done exists -- already complete, exiting."
    exit 0
fi

# Symlink fixed mesh files from fix/.
for f in hgrid.gr3 hgrid.ll vgrid.in estuary.gr3; do
    [ -L "$f" ] || ln -sf {{FIXDIR}}/$f .
done

# Symlink .in file and executable from bin/.
[ -L "gen_3Dth_from_nc.in" ] || ln -sf {{BINDIR}}/gen_3Dth_from_nc.in .
[ -L "{{GEN3DTH_EXE}}" ]     || ln -sf {{BINDIR}}/{{GEN3DTH_EXE}} .

echo ""
echo "  REMINDER: using _noscaling executable -- expects unpacked floats."
echo "  REMINDER: lon=lon-360 is commented out -- both mesh and HYCOM on 0-360."
echo ""

# Run the executable.
./{{GEN3DTH_EXE}}

# Write sentinel file on success.
touch gen_3Dth.done
echo "=== gen_3Dth complete for month ${MONTH} ==="
