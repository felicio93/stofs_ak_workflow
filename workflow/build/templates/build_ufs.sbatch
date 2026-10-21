#!/bin/bash --login
# =============================================================================
# SLURM job template: build a UFS-Coastal executable (target kind: ufs).
# Rendered/submitted by workflow.build.builder. The sources (with submodules)
# are already checked out on the login node; this job needs no network.
#
# compile.sh is UFS-Coastal's own entry point: it purges and loads the
# modulefile modulefiles/ufs_<machine_id>.<rt_compiler>.lua (written by the
# workflow from the machine profile), runs cmake/make into BUILD_DIR and
# leaves tests/fv3_<compile_id>.exe and tests/modules.fv3_<compile_id>.lua.
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

echo "=== build {{TARGET}} (ufs): UFS-Coastal {{SOURCE_COMMIT}} ==="
echo "Build dir: {{UFS_BUILD_DIR}}"

# stale products of a previous build must not be installed by mistake
rm -f {{SRC_DIR}}/tests/fv3_{{COMPILE_ID}}.exe {{SRC_DIR}}/tests/modules.fv3_{{COMPILE_ID}}.lua

export BUILD_JOBS={{CPUS}}
export BUILD_DIR={{UFS_BUILD_DIR}}
cd {{SRC_DIR}}/tests
./compile.sh {{MACHINE_ID}} '{{MAKE_OPT}}' {{COMPILE_ID}} {{RT_COMPILER}} YES NO

# Install into M{ID}/bin/ and write BUILD_INFO.yaml + the sentinel
{{PY}} -m workflow.build.finish --config {{CONFIG_DIR}} --target {{TARGET}}
echo "=== build {{TARGET}} complete ==="
