import re
import sys
from pathlib import Path

from workflow.core.config import load_config, model_dir


def read_param_nml(mdir: Path) -> dict:
    param_nml_path = mdir / "fix" / "param.nml"

    if not param_nml_path.exists():
        print(f"ERROR: param.nml not found in {mdir / 'fix'}")
        sys.exit(1)

    params = {}
    content = param_nml_path.read_text()

    for line in content.splitlines():
        if "=" not in line:
            continue

        key, value = [x.strip() for x in line.split("=", 1)]
        params[key] = value.split("!")[0].strip()

    return params


def gen_ufs_configure_month(cfg: dict, ym: str):
    pid = cfg["project_id"]
    mdir = model_dir(cfg)

    template_path = mdir / "fix" / "ufs.configure"

    if not template_path.exists():
        print(f"ERROR: Template file not found: {template_path}")
        sys.exit(1)

    model_configure_path = (
        mdir / f"I{pid}" / f"I{pid}_{ym}" / "model_configure"
    )

    if not model_configure_path.exists():
        print(f"ERROR: model_configure not found: {model_configure_path}")
        sys.exit(1)

    # ------------------------------------------------------------
    # Get forecast length
    # ------------------------------------------------------------
    mc_content = model_configure_path.read_text()

    match = re.search(
        r"^nhours_fcst\s*:\s*(\d+)",
        mc_content,
        re.MULTILINE,
    )

    if not match:
        print(f"ERROR: Could not find nhours_fcst in {model_configure_path}")
        return False

    nhours_fcst = int(match.group(1))

    # ------------------------------------------------------------
    # Get timestep
    # ------------------------------------------------------------
    param_nml = read_param_nml(mdir)

    if "dt" not in param_nml:
        print(f"ERROR: dt not found in {mdir / 'fix' / 'param.nml'}")
        return False

    dt = int(float(param_nml["dt"]))

    # ------------------------------------------------------------
    # Values to customize
    # ------------------------------------------------------------
    replacements = {
        "MED_model": cfg["med_model"],
        "MED_petlist_bounds": cfg["med_petlist_bounds"],
        "MED_omp_num_threads": cfg["med_omp_num_threads"],

        "ATM_model": cfg["atm_model"],
        "ATM_petlist_bounds": cfg["atm_petlist_bounds"],
        "ATM_omp_num_threads": cfg["atm_omp_num_threads"],

        "OCN_model": cfg["ocn_model"],
        "OCN_petlist_bounds": cfg["ocn_petlist_bounds"],
        "OCN_omp_num_threads": cfg["ocn_omp_num_threads"],

        "coupling_mode": cfg["cpl_mode"],
        "meshloc": "element",
        "CouplingConfig": cfg["coupling_config"],

        "start_type": cfg["run_type"],
        "case_name": cfg["case_name"],
        "restart_n": cfg["restart_n"],
        "stop_n": nhours_fcst,
    }

    # ------------------------------------------------------------
    # Output
    # ------------------------------------------------------------
    out_path = (
        mdir
        / f"I{pid}"
        / f"I{pid}_{ym}"
        / "ufs.configure"
    )

    sentinel = out_path.parent / "gen_ufs_configure.done"

    if sentinel.exists() and out_path.exists():
        print(f"  gen_ufs_configure: {ym} already complete. Skipping.")
        return True

    print(f"--- gen_ufs_configure {ym} -> {out_path} ---")

    # ------------------------------------------------------------
    # Read template
    # ------------------------------------------------------------
    lines = template_path.read_text().splitlines()

    new_lines = []

    # Keep track of whether we are inside the runSeq block.
    in_runseq = False
    runseq_timestep_written = False

    for line in lines:

        # ========================================================
        # RUN SEQUENCE
        # ========================================================
        if line.strip() == "runSeq::":
            new_lines.append(line)
            in_runseq = True
            runseq_timestep_written = False
            continue

        if in_runseq:

            # Replace the template's @45 with @<dt>.
            #
            # The template contains:
            #
            #   @45
            #     ATM -> MED ...
            #
            # We want:
            #
            #   @60
            #     ATM -> MED ...
            #
            if re.match(r"^\s*@\d+\s*$", line):
                if not runseq_timestep_written:
                    new_lines.append(f"@{dt}")
                    runseq_timestep_written = True
                continue

            # End of runSeq block
            if line.strip() == "::":
                new_lines.append(line)
                in_runseq = False
                continue

            # Everything else inside runSeq is preserved exactly.
            new_lines.append(line)
            continue

        # ========================================================
        # NORMAL CONFIGURATION LINES
        # ========================================================
        replaced = False

        for key, value in replacements.items():

            # Match only the actual configuration key at the
            # beginning of a line.
            #
            # This prevents:
            #
            #   MED_model: cmeps
            #
            # from accidentally matching:
            #
            #   ATM_model = datm
            #
            # inside MED_attributes.
            pattern = rf"^(\s*){re.escape(key)}(\s*[:=])"

            match = re.match(pattern, line)

            if match:
                indentation = match.group(1)
                separator = match.group(2)

                new_lines.append(
                    f"{indentation}{key}{separator} {value}"
                )

                replaced = True
                break

        if replaced:
            continue

        # ========================================================
        # EVERYTHING ELSE
        # ========================================================
        #
        # Preserve the template line exactly.
        #
        new_lines.append(line)

    # ------------------------------------------------------------
    # Write output
    # ------------------------------------------------------------
    out_path.write_text("\n".join(new_lines) + "\n")

    return True


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate ufs.configure file for a given month."
    )

    parser.add_argument(
        "--config",
        required=True,
        help="Path to the config directory.",
    )

    parser.add_argument(
        "--month",
        required=True,
        help="Month in YYYYMM format.",
    )

    args = parser.parse_args()

    config = load_config(Path(args.config))

    if gen_ufs_configure_month(config, args.month):

        sentinel = (
            model_dir(config)
            / f"I{config['project_id']}"
            / f"I{config['project_id']}_{args.month}"
            / "gen_ufs_configure.done"
        )

        sentinel.touch()

        print(
            f"  Wrote {sentinel.parent / 'ufs.configure'}"
        )

        print(f"  Sentinel: {sentinel}")

