"""
models/schism_wwm/postprocess/plot_hs.py
=========================================
Plot significant wave height (Hs) from SCHISM+WWM out2d_*.nc output.
One JPEG frame per timestep, saved to D{ID}/D{ID}_{YYYYMM}/hs_frames/.

Usage:
    python -m workflow.models.schism_wwm.postprocess.plot_hs \
        --config <config_dir> --month YYYYMM
"""

import argparse
import gc
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.tri as mtri

from workflow.core.config import load_config, model_dir
from workflow.core.plot_style import read_mesh_boundaries


def _load_mesh(nc_path: Path):
    """Load mesh triangulation from a SCHISM out2d_*.nc file."""
    import xarray as xr
    drop = ['zcor', 'dryFlagNode', 'dryFlagElement', 'dryFlagSide']
    ds   = xr.open_dataset(str(nc_path),
                           drop_variables=[v for v in drop])
    x   = np.array(ds['SCHISM_hgrid_node_x'])
    y   = np.array(ds['SCHISM_hgrid_node_y'])
    dep = np.array(ds['depth'])
    raw = np.nan_to_num(
        np.array(ds['SCHISM_hgrid_face_nodes']), nan=0) - 1
    ds.close()

    if raw.shape[1] == 3:
        tris = raw.astype(int)
    else:
        col4   = raw[:, 3]
        is_tri = np.isnan(col4) | (col4 < 0)
        tris   = np.vstack([raw[is_tri, :3],
                            raw[~is_tri][:, :3],
                            raw[~is_tri][:, [0, 2, 3]]]).astype(int)

    dep = np.where(np.isnan(dep), -9999.0, dep)
    return x, y, dep, mtri.Triangulation(x, y, tris)


def _render_frame(triang, hs_vals, depth, time_str, out_path,
                  vmin, vmax, boundaries, isobaths, dpi=150):
    """Render one Hs frame."""
    from workflow.core.plot_style import (
        PADDING_LON, PADDING_LAT, TITLE_FS, LABEL_FS, TICK_FS, CBAR_FS,
        _draw_boundaries, _aspect_figsize,
    )

    if boundaries and 'mesh_extent' in boundaries:
        ext   = boundaries['mesh_extent']
        x_min = ext[0] - PADDING_LON; x_max = ext[1] + PADDING_LON
        y_min = ext[2] - PADDING_LAT; y_max = ext[3] + PADDING_LAT
    else:
        x_min = float(triang.x.min()); x_max = float(triang.x.max())
        y_min = float(triang.y.min()); y_max = float(triang.y.max())

    fw, fh = _aspect_figsize(x_min, x_max, y_min, y_max)
    fig, ax = plt.subplots(figsize=(fw, fh), constrained_layout=True)

    pcm = ax.tripcolor(triang, hs_vals, shading='flat',
                       cmap='turbo', vmin=vmin, vmax=vmax, rasterized=True)
    cbar = fig.colorbar(pcm, ax=ax, location='right',
                        pad=0.02, fraction=0.046, shrink=0.9)
    cbar.set_label('Hs (m)', fontsize=CBAR_FS)
    cbar.ax.tick_params(labelsize=TICK_FS)

    if isobaths and depth is not None:
        try:
            ax.tricontour(triang, depth,
                          levels=[float(v) for v in isobaths],
                          colors='k', linewidths=0.4, alpha=0.5, zorder=4)
        except Exception:
            pass

    _draw_boundaries(ax, boundaries)
    ax.set_xlim(x_min, x_max); ax.set_ylim(y_min, y_max)
    ax.set_xlabel('Longitude (°E)', fontsize=LABEL_FS)
    ax.set_ylabel('Latitude (°N)', fontsize=LABEL_FS)
    ax.tick_params(labelsize=TICK_FS)
    ax.set_aspect('equal')
    ax.set_title(f'Significant Wave Height — {time_str}',
                 fontsize=TITLE_FS, fontweight='bold', pad=10)
    fig.savefig(str(out_path), dpi=dpi, format='jpeg',
                bbox_inches='tight', pil_kwargs={'quality': 90})
    plt.close(fig)


def plot_hs_month(cfg: dict, ym: str):
    """Plot Hs for all timesteps in out2d_*.nc for month ym."""
    import xarray as xr

    pid  = cfg['project_id']
    mdir = model_dir(cfg)

    outputs_dir = mdir / f'R{pid}' / f'R{pid}_{ym}' / 'outputs'
    out_dir     = mdir / f'D{pid}' / f'D{pid}_{ym}' / 'hs_frames'
    out_dir.mkdir(parents=True, exist_ok=True)

    # sigWaveHeight may be in its own file or in out2d
    hs_files = sorted(outputs_dir.glob('sigWaveHeight_*.nc'),
                      key=lambda p: int(p.stem.split('_')[-1]))
    if not hs_files:
        hs_files = sorted(outputs_dir.glob('out2d_*.nc'),
                          key=lambda p: int(p.stem.split('_')[-1]))

    if not hs_files:
        print(f"  ERROR: no output files found in {outputs_dir}")
        return

    print(f"  Found {len(hs_files)} output stack(s) for {ym}")

    # Load mesh from first file
    print("  Loading mesh ...")
    x, y, depth, triang = _load_mesh(hs_files[0])
    print(f"  Mesh: {len(x):,} nodes")

    # Load boundaries
    boundaries = None
    for hp in (mdir / 'fix' / 'hgrid.ll', mdir / 'fix' / 'hgrid.gr3'):
        if hp.exists():
            try:
                boundaries = read_mesh_boundaries(hp)
                print(f"  Boundaries: {hp.name}")
            except Exception as exc:
                print(f"  WARNING boundaries: {exc}")
            break

    isobaths = cfg.get('isobaths', [200, 2000])

    # Color scale from first file
    print("  Computing color scale ...")
    with xr.open_dataset(str(hs_files[0])) as ds0:
        if 'sigWaveHeight' not in ds0:
            print(f"  ERROR: 'sigWaveHeight' not in {hs_files[0].name}")
            print(f"  Available: {list(ds0.data_vars)}")
            return
        hs0    = np.array(ds0['sigWaveHeight'])
        finite = hs0[np.isfinite(hs0) & (hs0 >= 0)]
        vmin   = 0.0
        vmax   = float(np.percentile(finite, 98)) if finite.size > 0 else 5.0
        vmax   = max(vmax, 0.5)

    print(f"  Color scale: 0 – {vmax:.2f} m")

    # Render frames
    n_done = 0
    for nc_file in hs_files:
        print(f"  Stack: {nc_file.name}")
        ds = xr.open_dataset(str(nc_file))

        if 'sigWaveHeight' not in ds:
            print(f"    'sigWaveHeight' not in file, skipping.")
            ds.close()
            continue

        times  = ds['time'].values
        hs_arr = np.array(ds['sigWaveHeight'])   # (time, node)
        ds.close()

        for t_idx in range(hs_arr.shape[0]):
            t       = times[t_idx]
            ts      = str(np.datetime_as_string(t, unit='s')
                         ).replace(':', '').replace('-', '').replace('T', '_')
            out     = out_dir / f'hs_{ts}.jpg'
            if out.exists():
                n_done += 1
                continue

            hs_vals  = hs_arr[t_idx].copy()
            hs_vals  = np.where(np.isfinite(hs_vals) & (hs_vals >= 0),
                                hs_vals, 0.0)
            time_str = str(np.datetime_as_string(t, unit='m')
                          ).replace('T', ' ')

            _render_frame(triang, hs_vals, depth, time_str, out,
                          vmin, vmax, boundaries, isobaths)
            n_done += 1
            print(f"    [{n_done}] {out.name}  (max Hs={hs_vals.max():.2f} m)")

        gc.collect()

    print(f"\n  {n_done} frame(s) written to {out_dir}")


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', required=True)
    ap.add_argument('--month',  required=True)
    args = ap.parse_args()
    plot_hs_month(load_config(Path(args.config)), args.month)
