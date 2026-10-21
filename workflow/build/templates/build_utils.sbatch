#!/bin/bash --login
# =============================================================================
# SLURM job template: build SCHISM utilities (target kinds hycom_utils,
# combine_hotstart, combine_output_mpi, partition_tools).
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

echo "=== build {{TARGET}} ({{KIND}}): SCHISM {{SOURCE_COMMIT}} ==="
echo "Build dir: $(pwd)"

NC=$(nc-config --prefix)
NF=$(nf-config --prefix)
mkdir -p bin

{{COMMANDS}}

# Install into M{ID}/bin/ and write BUILD_INFO.yaml + the sentinel
{{PY}} -m workflow.build.finish --config {{CONFIG_DIR}} --target {{TARGET}}
echo "=== build {{TARGET}} complete ==="
