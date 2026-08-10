"""
stofs_ak_workflow
=================
Automated pre-processing, run management, and post-processing workflow for the
SCHISM ocean model (and future coupled / UFS-Coastal variants), targeting the
STOFS-AK Alaska/Bering Sea domain.

Package layout
--------------
    workflow.core          Shared plumbing (config, mesh parsing, SLURM, envs, plotting).
    workflow.downloaders   Raw forcing-data fetchers (HYCOM, ERA5, GloFAS).
    workflow.diagnostics   Model-agnostic diagnostic plots (mesh, HYCOM, sflux).
    workflow.tidal         TPXO9 tidal database reader (vendored from pyschism).
    workflow.models        Per-model driver tree. Each model has preprocess/run/
                           postprocess submodules plus a Driver that the
                           orchestrator selects via project.yaml `model_type`.
"""
