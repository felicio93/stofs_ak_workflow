#!/bin/bash --login
# =============================================================================
# SLURM job for UFS-SCHISM per-stack combine + diagnostic frames.
# Submitted by auto_hotstart.py once per completed output stack.
#
# Steps:
#   1. Combine schout_??????_N.nc -> schout_N.nc (all variables)
#   2. For each configured diag_run_var, render diagnostic frames
#   3. Delete per-rank partition files for this stack
#
# Environment variables passed via --export at submit time:
#   COMBINE_DIAG_STACK : stack number to process (e.g. 1, 2, 3, ...)
#   COMBINE_DIAG_MONTH : YYYYMM label for logging
# =============================================================================
#SBATCH -D ./outputs
#SBATCH -J {{COMBINE_DIAG_JOBNAME}}
#SBATCH -A {{ACCOUNT}}
#SBATCH -p {{PARTITION}}
#SBATCH -N {{COMBINE_DIAG_NODES}}
#SBATCH -n {{COMBINE_DIAG_NRANKS}}
#SBATCH --exclusive
#SBATCH -t {{COMBINE_DIAG_WALLTIME}}
#SBATCH -o {{LOGDIR}}/diag_oldio_%j.out
#SBATCH -e {{LOGDIR}}/diag_oldio_%j.err
#SBATCH --mail-user={{MAILUSER}}
#SBATCH --mail-type=FAIL

set -e
ulimit -s unlimited

module use modulefiles
module load modules.fv3_datm2sch

echo "=== diag_oldio: stack ${COMBINE_DIAG_STACK} month ${COMBINE_DIAG_MONTH} ==="
echo "Working dir: $(pwd)"

# --- Step 1: Combine this stack (all variables) ---
COMBINED="schout_${COMBINE_DIAG_STACK}.nc"

if [ -f "${COMBINED}" ]; then
    echo "  ${COMBINED} already exists, skipping combine step."
else
    echo "  Combining stack ${COMBINE_DIAG_STACK} with {{COMBINE_DIAG_NRANKS}} ranks..."
    srun ./combine_output11_MPI \
        -b ${COMBINE_DIAG_STACK} \
        -e ${COMBINE_DIAG_STACK} \
        -w 0 \
        -o schout
    echo "  Combine complete: ${COMBINED}"
fi

# Touch intermediate sentinel so we know combine succeeded
touch "combine_${COMBINE_DIAG_STACK}.done"

# --- Step 2: Render diagnostic frames (one array task per variable) ---
echo "  Rendering diagnostic frames..."
{{PY}} {{SCRIPT}} \
    --config {{CONFIG_DIR}} \
    --month  ${COMBINE_DIAG_MONTH} \
    --stack  ${COMBINE_DIAG_STACK} \
    --varlist "{{DIAG_VARLIST}}"

# --- Step 3: Delete per-rank partition files for this stack ---
echo "  Cleaning partition files for stack ${COMBINE_DIAG_STACK}..."
n_deleted=0
freed_bytes=0
for f in schout_??????_${COMBINE_DIAG_STACK}.nc; do
    [ -f "$f" ] || continue
    sz=$(stat -c %s "$f" 2>/dev/null || echo 0)
    freed_bytes=$((freed_bytes + sz))
    rm -f "$f"
    n_deleted=$((n_deleted + 1))
done
freed_gb=$(echo "scale=2; $freed_bytes / 1073741824" | bc 2>/dev/null || echo "?")
echo "  Deleted ${n_deleted} partition file(s) (~${freed_gb} GB freed)."

echo "=== diag_oldio stack ${COMBINE_DIAG_STACK} complete ==="
