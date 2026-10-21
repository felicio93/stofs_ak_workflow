#!/bin/bash --login
# =============================================================================
# SLURM job array template for gen_nudge (one task per group).
# Rendered/submitted by workflow.models.schism.preprocess.gen_hycom_utils.
# gen_nudge_from_nc.in is written per-group into I{ID}_{group_id}/ by
# gen_hycom_utils.py — it is already present in IDIR, no symlink needed.
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

# The utilities crash on Hercules with the default stack size
ulimit -s unlimited || echo "WARNING: could not raise the stack limit"

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

# gen_nudge_from_nc.in is already in IDIR (written by gen_hycom_utils.py)
# — no symlink needed, just verify it exists
if [ ! -f "gen_nudge_from_nc.in" ]; then
    echo "ERROR: gen_nudge_from_nc.in not found in ${IDIR}"
    echo "  Run:  stofs-ak --run --only gen_nudge --config <cfg>"
    exit 1
fi

# Symlink executable from bin/
[ -L "{{GENNUDGE_EXE}}" ] || ln -sf {{BINDIR}}/{{GENNUDGE_EXE}} .

echo ""
echo "  Executable: {{GENNUDGE_EXE}}  (expects UNPACKED float HYCOM data)"
echo ""

# Run the executable
./{{GENNUDGE_EXE}}

# A Fortran `stop` exits with status 0, so the exit code alone cannot tell
# success from failure.  The utilities write fatal errors to fort.11, which
# is created for every run and stays empty on success.
if [ -s fort.11 ]; then
    echo "ERROR: {{GENNUDGE_EXE}} reported a fatal error (fort.11):"
    cat fort.11
    exit 1
fi
for f in TEM_nu.nc SAL_nu.nc; do
    if [ ! -s "$f" ] || ncdump -h "$f" | grep -q "UNLIMITED ; // (0 currently)"; then
        echo "ERROR: $f is missing or has no time records"
        exit 1
    fi
done

# Write sentinel on success
touch gen_nudge.done
echo "=== gen_nudge complete for group ${GROUP} ==="
