#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_3Dth (one task per group).
# Rendered/submitted by workflow.models.schism.preprocess.gen_hycom_utils.
# gen_3Dth_from_nc.in is written per-group into I{ID}_{group_id}/ by
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
#SBATCH -o {{LOGDIR}}/gen_3Dth_%a.out
#SBATCH -e {{LOGDIR}}/gen_3Dth_%a.err
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
echo "=== gen_3Dth: array task ${SLURM_ARRAY_TASK_ID} -> group ${GROUP} ==="

IDIR={{IBASEDIR}}/I{{PID}}_${GROUP}
cd "${IDIR}" || { echo "ERROR: cannot cd to ${IDIR}"; exit 1; }
echo "Working dir: $(pwd)"

# Skip if already completed
if [ -f "gen_3Dth.done" ]; then
    echo "gen_3Dth.done exists -- already complete, exiting."
    exit 0
fi

# Symlink fixed mesh files from fix/
for f in hgrid.gr3 hgrid.ll vgrid.in estuary.gr3; do
    [ -L "$f" ] || ln -sf {{FIXDIR}}/$f .
done

# Symlink .in file from IDIR (written per-group by gen_hycom_utils.py)
# and executable from bin/
[ -L "gen_3Dth_from_nc.in" ] || ln -sf "${IDIR}/gen_3Dth_from_nc.in" .
[ -L "{{GEN3DTH_EXE}}" ]     || ln -sf {{BINDIR}}/{{GEN3DTH_EXE}} .

echo ""
echo "  REMINDER: _noscaling executable -- expects unpacked floats."
echo "  REMINDER: lon=lon-360 is commented out -- both mesh and HYCOM on 0-360."
echo ""

# Run the executable
./{{GEN3DTH_EXE}}

# Write sentinel on success
touch gen_3Dth.done
echo "=== gen_3Dth complete for group ${GROUP} ==="
