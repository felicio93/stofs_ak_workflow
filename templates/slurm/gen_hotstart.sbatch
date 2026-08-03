#!/bin/bash --login
# =============================================================================
# SLURM job template for gen_hotstart (single job, first month only).
# Rendered/submitted by workflow/submit_schism.py.
# Placeholders in {{ }} are substituted before submission.
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

# Load runtime libraries (must match the modules used to compile the executables).
module load intel-oneapi-compilers/2022.2.1
module load intel-oneapi-mpi/2021.7.1
module load netcdf-fortran/4.6.0
module load netcdf-c/4.9.0

echo "=== gen_hotstart for month {{MONTH}} ==="
echo "Working dir: $(pwd)"

# Skip if already completed.
if [ -f "gen_hotstart.done" ]; then
    echo "gen_hotstart.done exists -- already complete, exiting."
    exit 0
fi

# Symlink fixed mesh files from fix/.
for f in hgrid.gr3 hgrid.ll vgrid.in estuary.gr3; do
    [ -L "$f" ] || ln -sf {{FIXDIR}}/$f .
done

# Symlink .in file and executable from bin/.
[ -L "gen_hot_from_nc.in" ]  || ln -sf {{BINDIR}}/gen_hot_from_nc.in .
[ -L "{{HOTSTART_EXE}}" ]    || ln -sf {{BINDIR}}/{{HOTSTART_EXE}} .

echo ""
echo "  REMINDER: using _noscaling executable -- expects unpacked floats."
echo "  REMINDER: lon=lon-360 is commented out -- both mesh and HYCOM on 0-360."
echo ""

# Run the executable.
./{{HOTSTART_EXE}}

# Write sentinel file on success.
touch gen_hotstart.done
echo "=== gen_hotstart complete ==="
