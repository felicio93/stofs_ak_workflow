#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_nudge (one task per month).
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
#SBATCH -o {{LOGDIR}}/gen_nudge_%a.out
#SBATCH -e {{LOGDIR}}/gen_nudge_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Map array index to month string via manifest.
MONTH=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_nudge: array task ${SLURM_ARRAY_TASK_ID} -> month ${MONTH} ==="

IDIR={{IBASEDIR}}/I{{PID}}_${MONTH}
cd "${IDIR}" || { echo "ERROR: cannot cd to ${IDIR}"; exit 1; }
echo "Working dir: $(pwd)"

# Skip if already completed.
if [ -f "gen_nudge.done" ]; then
    echo "gen_nudge.done exists -- already complete, exiting."
    exit 0
fi

# Symlink fixed mesh files from fix/.
for f in hgrid.gr3 hgrid.ll vgrid.in TEM_nudge.gr3; do
    [ -L "$f" ] || ln -sf {{FIXDIR}}/$f .
done

# Symlink .in file and executable from bin/.
[ -L "gen_nudge_from_nc.in" ] || ln -sf {{BINDIR}}/gen_nudge_from_nc.in .
[ -L "{{GENNUDGE_EXE}}" ]     || ln -sf {{BINDIR}}/{{GENNUDGE_EXE}} .

echo ""
echo "  REMINDER: using _noscaling executable -- expects unpacked floats."
echo "  REMINDER: lon=lon-360 is commented out -- both mesh and HYCOM on 0-360."
echo ""

# Run the executable.
./{{GENNUDGE_EXE}}

# Write sentinel file on success.
touch gen_nudge.done
echo "=== gen_nudge complete for month ${MONTH} ==="
