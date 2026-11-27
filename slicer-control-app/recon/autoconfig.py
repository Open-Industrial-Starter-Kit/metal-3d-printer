# autoconfig.py
"""Shape analysis, settings recommendation and candidate search.

Two tiers, both user-triggered from the dashboard - nothing here runs on its own:

  Tier 1  analyze_shape() -> recommend_settings()   instant, rule based
  Tier 2  AutoTuneWorker                            reconstructs several candidates
                                                    and scores them against the cloud
"""
import traceback

import numpy as np
import open3d as o3d
from scipy.spatial import KDTree

from recon.utils import build_poly_design_matrix

EPS = 1e-12

# a smooth candidate must still reach this share of the scanned points
MIN_COVERAGE = 0.85

# Ceiling on Poisson artifact trim while the base is being sealed. Past this the
# surface starts breaking into islands, and reconstruct() keeps only the largest.
SEAL_MAX_TRIM = 12

METHOD_NAMES = {
    0: "Poisson Surface Reconstruction",
    1: "Ball Pivoting Algorithm (BPA)",
    2: "B-Spline / NURBS Surface",
    3: "Polynomial Surface Fitting",
}


# ---------------------------------------------------------------- descriptors

def analyze_shape(pcd, sample_cap=20000, seed=0):
    """Measure the geometric properties that decide which algorithm can work.

    Everything is computed in the cloud's own PCA frame, so a part that is
    rotated or tilted is described the same way as one sitting on the axes.
    """
    pts = np.asarray(pcd.points)
    n_points = len(pts)
    if n_points < 16:
        return None

    rng = np.random.default_rng(seed)
    if n_points > sample_cap:
        pts_s = pts[rng.choice(n_points, sample_cap, replace=False)]
    else:
        pts_s = pts

    # PCA frame: columns ordered major, mid, minor
    centre = pts_s.mean(axis=0)
    centred = pts_s - centre
    evals, evecs = np.linalg.eigh(centred.T @ centred / len(centred))
    axes = evecs[:, ::-1]
    local = centred @ axes
    evals_desc = evals[::-1]

    planarity = float(evals_desc[2] / max(evals_desc.sum(), EPS))
    bbox_diag = float(np.linalg.norm(pts.max(axis=0) - pts.min(axis=0)))

    # neighbourhood statistics
    k = int(min(16, len(pts_s) - 1))
    tree = KDTree(pts_s)
    dists, neighbours = tree.query(pts_s, k=k + 1, workers=-1)
    nn = dists[:, 1]
    spacing = float(np.median(nn))
    density_cv = float(np.std(nn) / max(np.mean(nn), EPS))

    # local plane residual: sqrt of the smallest eigenvalue of each neighbourhood
    patches = pts_s[neighbours]
    patches = patches - patches.mean(axis=1, keepdims=True)
    covs = np.einsum('mki,mkj->mij', patches, patches) / (k + 1)
    patch_evals = np.linalg.eigvalsh(covs)
    noise_sigma = float(np.sqrt(np.maximum(patch_evals[:, 0], 0.0)).mean())
    curvature = patch_evals[:, 0] / np.maximum(patch_evals.sum(axis=1), EPS)
    sharp_fraction = float(np.mean(curvature > 0.02))

    # normal scatter: isotropy near 0 means a flat sheet, near 1 a closed volume.
    # Built from the outer product so the unoriented normal sign cannot matter.
    probe = o3d.geometry.PointCloud()
    probe.points = o3d.utility.Vector3dVector(pts_s)
    probe.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamKNN(knn=min(30, k)))
    normals = np.asarray(probe.normals)
    scatter = np.linalg.eigvalsh(normals.T @ normals / max(len(normals), 1))
    normal_isotropy = float(3.0 * scatter[0] / max(scatter.sum(), EPS))

    multi_valued_fraction = _multi_valued_fraction(local, spacing, noise_sigma)

    # z = f(x, y) fitting is only defined when the surface is single valued.
    # The layer count decides this; normal isotropy is only a backstop, because a
    # strongly curved OPEN sheet also spreads its normals widely.
    height_field_ok = multi_valued_fraction < 0.02 and normal_isotropy < 0.85

    return {
        'n_points': n_points,
        'bbox_diag': bbox_diag,
        'spacing': spacing,
        'density_cv': density_cv,
        'noise_sigma': noise_sigma,
        'relative_noise': float(noise_sigma / max(spacing, EPS)),
        'planarity': planarity,
        'sharp_fraction': sharp_fraction,
        'normal_isotropy': normal_isotropy,
        'multi_valued_fraction': multi_valued_fraction,
        'height_field_ok': bool(height_field_ok),
        'pca_points': local,
    }


def _multi_valued_fraction(local, spacing, noise_sigma, grid=48, min_per_cell=4):
    """Fraction of occupied XY cells holding more than one layer of surface.

    This, not overall height, is what rules out the height field methods: a
    steep wall is single valued and fits fine, a top-and-bottom shell does not.
    """
    u, v, w = local[:, 0], local[:, 1], local[:, 2]
    span_u = max(u.max() - u.min(), EPS)
    span_v = max(v.max() - v.min(), EPS)

    iu = np.clip(((u - u.min()) / span_u * grid).astype(int), 0, grid - 1)
    iv = np.clip(((v - v.min()) / span_v * grid).astype(int), 0, grid - 1)
    cell_id = iu * grid + iv

    order = np.argsort(cell_id, kind='stable')
    cell_sorted, w_sorted = cell_id[order], w[order]
    boundaries = np.flatnonzero(np.diff(cell_sorted)) + 1
    gap_threshold = max(4.0 * spacing, 3.0 * noise_sigma)

    occupied = 0
    layered = 0
    for chunk in np.split(w_sorted, boundaries):
        if len(chunk) < min_per_cell:
            continue
        occupied += 1
        column = np.sort(chunk)
        if np.any(np.diff(column) > gap_threshold):
            layered += 1

    return float(layered / occupied) if occupied else 0.0


# ------------------------------------------------------------- recommendation

def recommend_settings(desc, pcd, gap_baseline=None, seal_base=False):
    """Rule based Tier 1 pick: an algorithm plus every slider value it needs.

    `seal_base` mirrors the Seal Solid Bottom Base toggle. It does not change
    which algorithm is chosen - the scan is whatever shape it is - but it does
    constrain Poisson trimming, which sealing cannot survive at full strength.
    """
    reasons = []
    n = desc['n_points']
    spacing = max(desc['spacing'], EPS)
    rel_noise = desc['relative_noise']
    uniform = desc['density_cv'] < 0.45
    noisy = rel_noise > 0.25

    # Does a low-order polynomial already explain the surface down to the noise?
    # That, not "looks flat", is what makes polynomial fitting the right tool.
    poly_degree, poly_rms = _best_poly_fit(pcd)
    poly_tolerance = max(2.5 * desc['noise_sigma'], 0.5 * spacing)
    poly_explains = poly_rms <= poly_tolerance

    # --- algorithm
    if not desc['height_field_ok']:
        method = 0
        reasons.append(
            f"Surface is multi-valued ({desc['multi_valued_fraction']:.0%} of cells hold "
            f"more than one layer, normal isotropy {desc['normal_isotropy']:.2f}) - "
            "only Poisson can close a volume like this."
        )
    elif poly_explains:
        method = 3
        reasons.append(
            f"A degree {poly_degree} polynomial already fits to {poly_rms:.3g}, inside the "
            f"{poly_tolerance:.3g} noise tolerance - the simplest model that explains the surface."
        )
    elif noisy:
        method = 2
        reasons.append(
            f"Open sheet with noise at {rel_noise:.0%} of point spacing - "
            "spline smoothing suppresses it instead of meshing it."
        )
    elif uniform and desc['sharp_fraction'] > 0.15:
        method = 1
        reasons.append(
            f"Open sheet, clean and evenly sampled (density CV {desc['density_cv']:.2f}) "
            f"with {desc['sharp_fraction']:.0%} high-curvature points - BPA keeps that detail."
        )
    else:
        method = 2
        reasons.append("Open, smooth sheet - a spline sheet gives the most machinable surface.")

    # --- parameters that follow from the measurements
    knn = int(np.clip(round(20 * (1 + 3 * rel_noise)), 15, 80))
    reasons.append(f"KNN {knn}: scaled by measured noise so normals average it out.")

    depth = int(np.clip(round(np.log2(max(desc['bbox_diag'], EPS) / spacing)), 5, 10))
    trim = int(np.clip(round(100 * (0.02 + 0.10 * desc['density_cv'])), 0, 50)) if method == 0 else 0

    if method == 0 and seal_base and trim > SEAL_MAX_TRIM:
        # Trimming drops low-density vertices, which can break the mesh into
        # islands; reconstruct() then keeps only the largest one and the rest of
        # the surface is gone. Sealing makes that worse, because the skirt is
        # extruded from whatever boundary survived - so hold trim down and let
        # the connected-cluster pass do the artifact removal instead.
        reasons.append(
            f"Artifact trim held at {SEAL_MAX_TRIM / 100:.2f} instead of {trim / 100:.2f}: "
            "with the base sealed, heavier trimming fragments the surface and only the "
            "largest piece survives to be extruded."
        )
        trim = SEAL_MAX_TRIM

    # radius ~2x median spacing, expressed against the p95 gap the worker scales by
    if gap_baseline is None:
        gap_baseline = _gap_baseline(pcd, spacing)
    bpa = int(np.clip(round(10.0 * (2.0 * spacing) / max(gap_baseline, EPS)), 2, 60))
    voxel = 10 if desc['density_cv'] > 0.60 else 0

    grid_res = int(np.clip(round(np.sqrt(n) / 2), 10, 120))

    # scipy's guidance for SmoothBivariateSpline with unit weights is s ~ N * sigma^2,
    # and the worker computes s = (slider / 10) * N, so the slider wants 10 * sigma^2.
    spline_raw = 10.0 * desc['noise_sigma'] ** 2
    spline_smooth = int(np.clip(round(spline_raw), 1, 100))
    if method == 2 and not (1 <= spline_raw <= 100):
        reasons.append(
            f"Spline smoothing wanted {spline_raw:.3g} but the slider only spans 1-100, "
            "so it was clamped - this mapping is tied to your model's units (see notes)."
        )

    if method == 0:
        reasons.append(
            f"Octree depth {depth}: log2(diagonal {desc['bbox_diag']:.3g} / spacing "
            f"{spacing:.3g}) puts the finest cell at one point spacing."
        )
        if trim:
            reasons.append(f"Artifact trim {trim / 100:.2f}: density CV {desc['density_cv']:.2f} "
                           "means some low-density spurious vertices are likely.")
    elif method == 1:
        reasons.append(f"Ball radius {bpa / 10:.1f}x: about twice the median point spacing.")
        if voxel:
            reasons.append(f"Voxel filter on: density CV {desc['density_cv']:.2f} is uneven "
                           "enough that BPA would otherwise leave holes.")
    elif method == 3:
        reasons.append(f"Polynomial degree {poly_degree}: lowest BIC over degrees 1-5.")

    settings = {
        'method_idx': method,
        'knn': knn,
        'depth': depth,
        'trim': trim,
        'voxel': voxel,
        'bpa': bpa,
        'grid_res': grid_res,
        'spline_smooth': spline_smooth,
        'poly_degree': poly_degree,
        'height_field_ok': desc['height_field_ok'],
    }
    return settings, reasons


def _gap_baseline(pcd, fallback):
    """The 95th percentile nearest-neighbour gap, matching FileLoadWorker."""
    pts = np.asarray(pcd.points)
    if len(pts) < 2:
        return fallback
    sample = pts if len(pts) <= 20000 else pts[np.random.default_rng(0).choice(len(pts), 20000, replace=False)]
    dists, _ = KDTree(sample).query(sample, k=2, workers=-1)
    return float(np.percentile(dists[:, 1], 95))


def _best_poly_fit(pcd, max_degree=5, sample_cap=5000):
    """Pick the polynomial order by BIC and report its residual RMS.

    BIC keeps extra terms from being added for free; the residual then says
    whether that best polynomial is good enough to use at all.
    """
    pts = np.asarray(pcd.points)
    if len(pts) > sample_cap:
        pts = pts[np.random.default_rng(0).choice(len(pts), sample_cap, replace=False)]
    x, y, z = pts[:, 0], pts[:, 1], pts[:, 2]
    n = len(x)

    best_degree, best_bic, best_rms = 2, np.inf, np.inf
    for degree in range(1, max_degree + 1):
        A = build_poly_design_matrix(x, y, degree)
        if A.shape[1] >= n:
            break
        coefficients, _, _, _ = np.linalg.lstsq(A, z, rcond=None)
        residual = A @ coefficients - z
        rss = float(np.sum(residual ** 2))
        bic = n * np.log(max(rss / n, EPS)) + A.shape[1] * np.log(n)
        if bic < best_bic:
            best_degree, best_bic = degree, bic
            best_rms = float(np.sqrt(rss / n))
    return best_degree, best_rms


# -------------------------------------------------------------------- scoring

def surface_only(mesh, surface_triangles):
    """The reconstructed surface, with any sealing floor and skirt dropped.

    Sealing bolts a flat floor and vertical walls onto the mesh, and the scan
    holds no points behind either of them. Measuring fidelity on the sealed mesh
    therefore charges every candidate for geometry the user asked for: overreach
    counts the whole skirt as invented surface, and roughness counts the 90
    degree kink where it meets the boundary. Both scale with part height, so the
    taller the job the harder the search was penalised for doing as it was told.
    """
    if mesh is None or surface_triangles is None:
        return mesh
    triangles = np.asarray(mesh.triangles)
    if not 0 < surface_triangles < len(triangles):
        return mesh

    sub = o3d.geometry.TriangleMesh()
    sub.vertices = o3d.utility.Vector3dVector(np.asarray(mesh.vertices))
    sub.triangles = o3d.utility.Vector3iVector(triangles[:surface_triangles])
    sub.remove_unreferenced_vertices()
    return sub


def score_mesh(mesh, probe_points, noise_sigma, spacing, expect_closed, target_triangles,
               surface_triangles=None):
    """Lower is better. Fidelity is aimed AT the noise floor, never below it.

    Minimising residual alone always crowns the most flexible candidate, which
    just remodels the scanner noise as surface bumps. Coverage and manifoldness
    dominate the score because an unmachinable mesh is worthless regardless of
    how closely it hugs the points.

    Fidelity is judged on the reconstructed surface alone; the structural facts
    reported back describe the whole mesh, since that is what gets exported.
    """
    metrics = {'rms': np.inf, 'coverage': 0.0, 'overreach': 1.0, 'triangles': 0,
               'watertight': False, 'manifold': False}
    if mesh is None or len(mesh.triangles) == 0:
        return np.inf, metrics

    surface = surface_only(mesh, surface_triangles)
    if surface is None or len(surface.triangles) == 0:
        return np.inf, metrics

    scene = o3d.t.geometry.RaycastingScene()
    scene.add_triangles(o3d.t.geometry.TriangleMesh.from_legacy(surface))
    distances = scene.compute_distance(
        o3d.core.Tensor(probe_points.astype(np.float32))
    ).numpy().astype(np.float64)

    rms = float(np.sqrt(np.mean(distances ** 2)))
    tolerance = max(3.0 * noise_sigma, 0.5 * spacing)
    coverage = float(np.mean(distances <= tolerance))

    # The opposite error to coverage: mesh surface that no data supports. Without
    # this, Poisson wins every open sheet by sealing it into a blob - the hidden
    # underside costs nothing on coverage but is not a surface you can machine.
    # Judged at a looser distance than coverage on purpose: a coarse mesh passing
    # between points is a smooth approximation, not invented geometry.
    overreach = _overreach(surface, probe_points, max(3.0 * noise_sigma, 2.0 * spacing))
    triangles = len(mesh.triangles)
    watertight = bool(mesh.is_watertight())
    manifold = bool(mesh.is_edge_manifold())

    noise_floor = max(noise_sigma, 0.05 * spacing, EPS)
    fit_penalty = abs(np.log2(max(rms, EPS) / noise_floor))       # 0 when rms == sigma
    coverage_penalty = 4.0 * (1.0 - coverage)
    overreach_penalty = 3.0 * overreach
    manifold_penalty = 0.0 if manifold else 2.0
    closed_penalty = (0.0 if watertight else 0.75) if expect_closed else 0.0
    complexity_penalty = 0.25 * max(0.0, np.log2(max(triangles, 1) / max(target_triangles, 1)))

    score = (fit_penalty + coverage_penalty + overreach_penalty + manifold_penalty
             + closed_penalty + complexity_penalty)

    metrics.update({'rms': rms, 'coverage': coverage, 'overreach': overreach,
                    'triangles': triangles, 'watertight': watertight, 'manifold': manifold,
                    'fit_penalty': float(fit_penalty)})
    return float(score), metrics


def roughness_degrees(mesh):
    """Mean dihedral angle between neighbouring triangles, in degrees.

    This is the number that matches what "smooth" means to a machinist: how
    sharply the surface kinks from one facet to the next. A rippled or noisy
    mesh scores high even when it hugs the points perfectly.
    """
    triangles = np.asarray(mesh.triangles)
    if len(triangles) < 2:
        return np.inf

    mesh.compute_triangle_normals()
    normals = np.asarray(mesh.triangle_normals)

    edges = np.vstack([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    edges = np.sort(edges, axis=1)
    owners = np.tile(np.arange(len(triangles)), 3)

    order = np.lexsort((edges[:, 1], edges[:, 0]))
    edges, owners = edges[order], owners[order]

    shared = np.all(edges[1:] == edges[:-1], axis=1)
    left, right = owners[:-1][shared], owners[1:][shared]
    if len(left) == 0:
        return np.inf

    cosines = np.clip(np.einsum('ij,ij->i', normals[left], normals[right]), -1.0, 1.0)
    return float(np.degrees(np.arccos(cosines)).mean())


def score_smooth(mesh, probe_points, noise_sigma, spacing, expect_closed, target_triangles,
                 surface_triangles=None):
    """Smoothest surface that still respects the data.

    Fidelity becomes a constraint rather than the objective: any candidate whose
    residual stays inside the noise tolerance is considered faithful enough, and
    among those the flattest-flowing mesh wins.
    """
    _, metrics = score_mesh(mesh, probe_points, noise_sigma, spacing,
                            expect_closed, target_triangles, surface_triangles)
    if not np.isfinite(metrics['rms']):
        return np.inf, metrics

    # Measured on the surface alone: the seam where a sealing skirt turns down
    # is a right angle by construction, and it would otherwise swamp the mean.
    roughness = roughness_degrees(surface_only(mesh, surface_triangles))
    metrics['roughness'] = roughness
    if not np.isfinite(roughness):
        return np.inf, metrics

    tolerance = max(3.0 * noise_sigma, 0.5 * spacing)
    drift = max(0.0, metrics['rms'] / max(tolerance, EPS) - 1.0)
    thin_coverage = max(0.0, MIN_COVERAGE - metrics['coverage'])

    # Roughness has to dominate: the other terms are guards that only bite once a
    # candidate stops being faithful, otherwise they quietly outvote the goal and
    # a visibly faceted mesh wins the "smooth" button.
    score = (roughness / 2.0                        # the objective
             + 6.0 * drift                          # residual must stay in tolerance
             + 5.0 * thin_coverage                  # and still reach the points
             + 3.0 * metrics.get('overreach', 0.0)  # without inventing surface
             + (0.0 if metrics['manifold'] else 2.0))
    return float(score), metrics


def _overreach(mesh, cloud_points, tolerance, samples=4000):
    """Fraction of the mesh's own area sitting far from any input point."""
    try:
        sampled = np.asarray(mesh.sample_points_uniformly(samples).points)
    except Exception:
        return 0.0
    if len(sampled) == 0:
        return 0.0
    distances, _ = KDTree(cloud_points).query(sampled, k=1, workers=-1)
    return float(np.mean(distances > tolerance))


# ----------------------------------------------------------------- Tier 2 search

def build_candidates(desc, base, max_candidates=8, seal_base=False):
    """Variations around the Tier 1 pick, best guess first."""
    candidates = []

    def add(label, **overrides):
        if len(candidates) < max_candidates:
            settings = dict(base)
            settings.update(overrides)
            candidates.append((label, settings))

    uniform = desc['density_cv'] < 0.45
    method = base['method_idx']
    # same reasoning as recommend_settings: a sealed base cannot be extruded
    # from a boundary that trimming has broken into pieces
    trim_ceiling = SEAL_MAX_TRIM if seal_base else 50

    def poisson_sweep():
        trim = min(base['trim'], trim_ceiling)
        for depth in sorted({max(5, base['depth'] - 1), base['depth'], min(10, base['depth'] + 1)}):
            add(f"Poisson depth {depth}, trim {trim / 100:.2f}",
                method_idx=0, depth=depth, trim=trim)
        if desc['density_cv'] > 0.35:
            heavier = min(trim + 6, trim_ceiling)
            if heavier > trim:
                add(f"Poisson depth {base['depth']}, trim {heavier / 100:.2f}",
                    method_idx=0, depth=base['depth'], trim=heavier)

    def bpa_sweep():
        for mult in sorted({base['bpa'], int(np.clip(base['bpa'] * 1.6, 2, 60))}):
            add(f"BPA radius {mult / 10:.1f}x", method_idx=1, bpa=mult)

    def spline_sweep():
        for smooth in sorted({base['spline_smooth'], int(np.clip(base['spline_smooth'] * 3, 1, 100))}):
            add(f"Spline smoothing {smooth / 10:.1f}", method_idx=2, spline_smooth=smooth)

    def poly_sweep():
        for degree in sorted({max(1, base['poly_degree'] - 1), base['poly_degree'],
                              min(5, base['poly_degree'] + 1)}):
            add(f"Polynomial degree {degree}", method_idx=3, poly_degree=degree)

    # recommended family first, then the legal alternatives
    order = {0: [poisson_sweep], 1: [bpa_sweep], 2: [spline_sweep], 3: [poly_sweep]}[method]
    for sweep in order:
        sweep()

    if method != 0:
        poisson_sweep()
    if desc['height_field_ok']:
        if method != 2:
            spline_sweep()
        if method != 3:
            poly_sweep()
    if uniform and method != 1:
        bpa_sweep()

    return candidates[:max_candidates]


def build_smooth_candidates(desc, base, max_candidates=8, seal_base=False):
    """Candidates biased toward a smooth surface rather than maximum fidelity.

    Ball pivoting is left out on purpose: it triangulates the points as they
    are, so it can only ever be as rough as the scan.
    """
    candidates = []

    def add(label, **overrides):
        if len(candidates) < max_candidates:
            settings = dict(base)
            settings.update(overrides)
            candidates.append((label, settings))

    if desc['height_field_ok']:
        for smooth in sorted({max(1, base['spline_smooth']),
                              int(np.clip(base['spline_smooth'] * 4, 2, 100)),
                              int(np.clip(base['spline_smooth'] * 12, 4, 100))}):
            add(f"Spline smoothing {smooth / 10:.1f}", method_idx=2, spline_smooth=smooth)
        for degree in sorted({base['poly_degree'], min(5, base['poly_degree'] + 1)}):
            add(f"Polynomial degree {degree}", method_idx=3, poly_degree=degree)

    # a coarser octree is the smoothing control for Poisson
    trim = min(base['trim'], SEAL_MAX_TRIM if seal_base else 50)
    for depth in sorted({max(5, base['depth'] - 2), max(5, base['depth'] - 1), base['depth']}):
        add(f"Poisson depth {depth} (smoothed)", method_idx=0, depth=depth,
            trim=trim, knn=int(np.clip(base['knn'] * 1.5, 15, 80)))

    return candidates[:max_candidates]


#: Above this point count, candidates are scored on a decimated copy. Scoring
#: reconstructs every candidate, so the cost is per-candidate, not one-off.
AUTOTUNE_DECIMATE_ABOVE = 200000


def auto_tune(pcd, desc, candidates, gap_baseline, seal_base, service,
              objective='fidelity', progress=None):
    """Reconstruct each candidate and score it against the original cloud.

    Returns ``(results, note)``; *results* is sorted best-first. *progress* is
    an optional ``fn(index, total, label)`` called before each candidate.

    A background thread in the original project. It is a plain function here so
    this module stays free of Qt bindings -- ui/pages/ply_viewer.py runs it off
    the GUI thread and turns the callback into a signal.
    """
    from recon.workers import settings_to_params
    from recon.isolate import ReconstructionFailed, to_mesh

    def report(index, total, label):
        if progress is not None:
            progress(index, total, label)

    note = ""
    source = pcd
    n = len(source.points)
    if n > AUTOTUNE_DECIMATE_ABOVE:
        source = source.random_down_sample(AUTOTUNE_DECIMATE_ABOVE / n)
        note = (f"Scored on a {len(source.points):,} point decimated copy "
                f"of {n:,} points - settings are applied to the full cloud.")
    service.set_cloud(np.asarray(source.points))

    points = np.asarray(pcd.points)
    rng = np.random.default_rng(0)
    probe = points if len(points) <= 8000 else points[rng.choice(len(points), 8000, replace=False)]

    # Only the measured shape decides this. Sealing used to force it on, which
    # meant the 0.75 non-watertight penalty landed on every open-sheet
    # candidate while a Poisson blob - watertight on its own - collected
    # nothing, handing the search to exactly the result the overreach term
    # exists to prevent. A candidate does not need to close itself when the
    # seal is going to close it afterwards.
    expect_closed = not desc['height_field_ok']
    target_triangles = max(2000, len(points))

    results = []
    total = len(candidates)
    for index, (label, settings) in enumerate(candidates):
        report(index, total, label)
        params = settings_to_params(settings, gap_baseline, seal_base)
        failed_metrics = {'rms': np.inf, 'coverage': 0.0, 'triangles': 0,
                          'watertight': False, 'manifold': False}
        scorer = score_smooth if objective == 'smooth' else score_mesh
        warning = None
        try:
            vertices, triangles, normals, info = service.reconstruct(params)
            mesh = to_mesh(vertices, triangles, normals)
            score, metrics = scorer(mesh, probe, desc['noise_sigma'],
                                    desc['spacing'], expect_closed, target_triangles,
                                    info.get('surface_triangles'))
            error = None
            # a candidate that reconstructed fine but could not be sealed is
            # still a real result, so it stays ranked rather than being
            # reported as a failure
            warning = info.get('seal_error')
        except ReconstructionFailed as exc:
            score, metrics, error = np.inf, failed_metrics, str(exc)
        except Exception as exc:
            score, metrics, error = np.inf, failed_metrics, str(exc)
        results.append({'label': label, 'settings': settings, 'score': score,
                        'metrics': metrics, 'error': error, 'warning': warning})

    report(total, total, "done")
    results.sort(key=lambda item: item['score'])
    return results, note
