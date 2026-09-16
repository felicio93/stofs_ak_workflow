#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_nudge (one task per group).
# Rendered/submitted by workflow.models.schism.preprocess.gen_hycom_utils.
# gen_nudge_from_nc.in is written per-group into I{ID}_{group_id}/ by
# gen_hycom_utils.py and symlinked from there (not from bin/).
# =============================================================================
#SBATCH -D {{WORKDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH --array=1-{{NGROUPS}}
#SBATCH -n 1
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_nudge_%a.out
#SBATCH -e {{LOGDIR}}/gen_nudge_%a.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

# Load runtime libraries
module load intel-oneapi-compilers/2022.2.1
module load intel-oneapi-mpi/2021.7.1
module load netcdf-fortran/4.6.0
module load netcdf-c/4.9.0

# Map array index to group ID via manifest
GROUP=$(sed -n "${SLURM_ARRAY_TASK_ID}p" {{MANIFEST}})
echo "=== gen_nudge: array task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP} ==="

IDIR={{IBASEDIR}}/I{{PID}}_${GROUP}
cd "${IDIR}" || { echo "ERROR: cannot cd to ${IDIR}"; exit 1; }
echo "Working dir: $(pwd)"

# Skip if already completed
if [ -f "gen_nudge.done" ]; then
    echo "gen_nudge.done exists -- already complete, exiting."
    exit 0
fi

# Symlink fixed mesh files from fix/
for f in hgrid.gr3 hgrid.ll vgrid.in TEM_nudge.gr3; do
    [ -L "$f" ] || ln -sf {{FIXDIR}}/$f .
done

# Symlink .in file from IDIR (written per-group by gen_hycom_utils.py)
# and executable from bin/
[ -L "gen_nudge_from_nc.in" ] || ln -sf "${IDIR}/gen_nudge_from_nc.in" .
[ -L "{{GENNUDGE_EXE}}" ]     || ln -sf {{BINDIR}}/{{GENNUDGE_EXE}} .

echo ""
echo "  REMINDER: _noscaling executable -- expects unpacked floats."
echo "  REMINDER: lon=lon-360 is commented out -- both mesh and HYCOM on 0-360."
echo ""

# Run the executable
./{{GENNUDGE_EXE}}

# Write sentinel on success
touch gen_nudge.done
echo "=== gen_nudge complete for group ${GROUP} ==="
