#!/bin/bash --login
# =============================================================================
# SLURM job template for gen_hotstart (single job, first group only).
# Rendered/submitted by workflow.models.schism.preprocess.gen_hycom_utils.
# gen_hot_from_nc.in is read from bin/ (no stack ceiling inside it).
# =============================================================================
#SBATCH -D {{IDIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --mem={{HOTSTART_MEM}}
#SBATCH -t {{HOTSTART_WALLTIME}}
#SBATCH -o {{LOGDIR}}/gen_hotstart.out
#SBATCH -e {{LOGDIR}}/gen_hotstart.err
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

echo "=== gen_hotstart for group {{GROUP}} ==="
echo "Working dir: $(pwd)"

# Skip if already completed
if [ -f "gen_hotstart.done" ]; then
    echo "gen_hotstart.done exists -- already complete, exiting."
    exit 0
fi

# Symlink fixed mesh files from fix/
for f in hgrid.gr3 hgrid.ll vgrid.in estuary.gr3; do
    [ -L "$f" ] || ln -sf {{FIXDIR}}/$f .
done

# Symlink .in file from bin/ and executable from bin/
[ -L "gen_hot_from_nc.in" ] || ln -sf {{BINDIR}}/gen_hot_from_nc.in .
[ -L "{{HOTSTART_EXE}}" ]   || ln -sf {{BINDIR}}/{{HOTSTART_EXE}} .

echo ""
echo "  Executable: {{HOTSTART_EXE}}  (expects UNPACKED float HYCOM data)"
echo ""

# Run the executable
./{{HOTSTART_EXE}}

# A Fortran `stop` exits with status 0, so the exit code alone cannot tell
# success from failure.  The utilities write fatal errors to fort.11, which
# is created for every run and stays empty on success.
if [ -s fort.11 ]; then
    echo "ERROR: {{HOTSTART_EXE}} reported a fatal error (fort.11):"
    cat fort.11
    exit 1
fi
if [ ! -s hotstart.nc ]; then
    echo "ERROR: hotstart.nc was not written"
    exit 1
fi

# Write sentinel on success
touch gen_hotstart.done
echo "=== gen_hotstart complete ==="
