#!/bin/bash --login
# =============================================================================
# SLURM job template: build standalone SCHISM (target kind: schism).
# Rendered/submitted by workflow.build.builder. The sources are already
# checked out (login node); this job needs no network.
# =============================================================================
#SBATCH -D {{BUILD_DIR}}
#SBATCH -J {{JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -n 1
#SBATCH --cpus-per-task={{CPUS}}
#SBATCH --mem={{MEM}}
#SBATCH -t {{WALLTIME}}
#SBATCH -o {{LOGDIR}}/build_{{TARGET}}.out
#SBATCH -e {{LOGDIR}}/build_{{TARGET}}.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=NONE

set -e

{{MODULE_LINES}}

echo "=== build {{TARGET}}: SCHISM {{SOURCE_COMMIT}} ==="
echo "Build dir: $(pwd)"

# Out-of-tree configure: the machine cache (compilers, NetCDF, flags) ships
# with SCHISM; the options come from build.yaml.
cmake -C {{SCHISM_CACHE}} {{CMAKE_DEFS}} {{SRC_DIR}}/src
make -j{{CPUS}} pschism

# Install into M{ID}/bin/ and write BUILD_INFO.yaml + the sentinel
{{PY}} -m workflow.build.finish --config {{CONFIG_DIR}} --target {{TARGET}}
echo "=== build {{TARGET}} complete ==="
