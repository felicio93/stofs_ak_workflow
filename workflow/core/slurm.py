"""
core/slurm.py
=============
Unified SLURM submission helper.

This consolidates the template-rendering and `sbatch` logic that was
previously duplicated across the (now removed) submit_era5.py,
submit_plots.py, submit_inspect_mesh.py, and submit_hycom_utils.py launchers.
Model drivers use a `SlurmSubmitter` instance to render `.sbatch` templates
(with `{{KEY}}` placeholders) and submit them.

Design notes
------------
* Templates live next to the code that uses them, e.g.
  workflow/models/schism/templates/slurm/*.sbatch. A driver constructs a
  SlurmSubmitter pointed at its own templates directory.
* Interpreters are always called by absolute path (never `conda activate`),
  resolved via workflow.core.environment.env_python.
"""

import shutil
import subprocess
import sys
from pathlib import Path


class SlurmSubmitter:
    """Render `{{KEY}}` templates and submit them with sbatch.

    Parameters
    ----------
    templates_dir : Path
        Directory containing the `.sbatch` templates for this model/step.
    require_sbatch : bool
        If True (default), verify `sbatch` is on PATH and exit otherwise.
        Set False for dry-run/testing on nodes without SLURM.
    """

    def __init__(self, templates_dir: Path, require_sbatch: bool = True):
        self.templates_dir = Path(templates_dir)
        if require_sbatch and shutil.which("sbatch") is None:
            print("ERROR: sbatch not found. Run from a node with SLURM "
                  "(e.g. a Hercules login node).")
            sys.exit(1)

    # -- rendering ---------------------------------------------------------

    def render(self, template_name: str, subs: dict) -> str:
        """Return the template text with every `{{KEY}}` replaced."""
        path = self.templates_dir / template_name
        if not path.exists():
            print(f"ERROR: SLURM template not found: {path}")
            sys.exit(1)
        text = path.read_text()
        for key, val in subs.items():
            text = text.replace("{{" + key + "}}", str(val))
        return text

    def write_rendered(self, template_name: str, subs: dict,
                       out_path: Path) -> Path:
        """Render a template and write it to `out_path`; return that path."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(self.render(template_name, subs))
        return out_path

    # -- submission --------------------------------------------------------

    def submit(self, script_path: Path, dependency: str = None) -> str:
        """Submit a rendered script with sbatch; return sbatch's stdout line
        (e.g. 'Submitted batch job 12345'). Exits on failure.

        dependency: optional SLURM dependency string passed via
            --dependency, e.g. 'afterok:12345' or 'afterok:12345:12346'.
        """
        cmd = ["sbatch"]
        if dependency:
            cmd += [f"--dependency={dependency}"]
        cmd.append(str(script_path))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ERROR: sbatch failed: {result.stderr.strip()}")
            sys.exit(1)
        out = result.stdout.strip()
        print(f"  {out}")
        return out

    @staticmethod
    def parse_jobid(sbatch_output: str) -> str:
        """Extract the numeric job ID from sbatch stdout.

        sbatch prints 'Submitted batch job 12345' (possibly with a trailing
        array spec like '12345_[1-3]'). Returns the bare job ID string
        (e.g. '12345'), suitable for use in a --dependency argument.
        """
        # Output is always "Submitted batch job <ID>" — take the last token
        # and strip any array bracket suffix.
        token = sbatch_output.strip().split()[-1]
        return token.split("_")[0]

    def render_and_submit(self, template_name: str, subs: dict,
                          out_path: Path, dependency: str = None) -> str:
        """Convenience: render -> write -> submit in one call.

        Returns the raw sbatch stdout line (use parse_jobid to extract the ID).
        """
        rendered = self.write_rendered(template_name, subs, out_path)
        print(f"  Rendered SLURM script: {rendered}")
        return self.submit(rendered, dependency=dependency)


def write_manifest(months, manifest_path: Path) -> Path:
    """Write a one-month-per-line manifest for SLURM array indexing.

    Array task N reads line N of this file to learn which month it handles.
    """
    manifest_path = Path(manifest_path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("\n".join(months) + "\n")
    return manifest_path


def wait_for_slurm_jobs(job_ids: list, poll_seconds: int = 30,
                        label: str = "preprocess SLURM jobs"):
    """Block until all given SLURM job IDs have left the queue.

    Polls `squeue` every `poll_seconds` seconds and returns when none of the
    job IDs appear in the queue. Empty / None IDs in the list are ignored.
    Used by `--phase all` to ensure Phase 3 async SLURM jobs (gen_hotstart,
    gen_3Dth, gen_nudge, gen_sflux, etc.) have completed before Phase 4
    (setup_run) starts checking for their output files.
    """
    import os
    import time
    import subprocess

    active = [str(j) for j in job_ids if j]
    if not active:
        return

    user = os.environ.get("USER", os.environ.get("LOGNAME", ""))
    queue_cmd = f"squeue -u {user} -h -o '%i'"

    # Normalise submitted IDs to their base job ID (parse_jobid already strips
    # any array suffix, but be defensive in case a raw array ID is passed).
    active_base = [j.split("_")[0] for j in active]

    print(f"\n  [--phase all] Waiting for {len(active)} {label} to complete "
          f"before starting the run phase ...")
    print(f"  Job IDs: {', '.join(active)}")

    while True:
        result = subprocess.run(queue_cmd, shell=True,
                                capture_output=True, text=True)
        # squeue lists ARRAY TASKS as '<jobid>_<taskid>' (e.g. 9575586_2) while
        # sbatch returns only the base '<jobid>' (9575586). Compare on the base
        # ID so an array job is still considered "running" while any of its
        # tasks remain queued/running. Matching the raw tokens would miss array
        # tasks and return immediately, letting setup_run race ahead of the
        # array outputs.
        queued_base = {tok.split("_")[0] for tok in result.stdout.split()}
        still_running = [j for j in active_base if j in queued_base]
        if not still_running:
            print(f"  All {label} have completed. Proceeding to run phase.")
            break
        print(f"  Still pending/running: {', '.join(still_running)}. "
              f"Checking again in {poll_seconds}s ...")
        time.sleep(poll_seconds)
