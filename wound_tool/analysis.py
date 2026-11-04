"""Wound healing scratch-assay analysis: the detection method.

This module holds the image analysis only. ``batch.py`` handles folders, files and outputs.

Overview of the method
----------------------
1. 0 h image -> scratch outline (``outline_0h``)
   a. Coarse outline: texture is low in the empty scratch and high in the cell sheet.
   b. Cell map from cell *shape*: spindle cells are thin ridges (Sato ridge filter); round,
      blurry debris is excluded, and straight spindle cells hidden inside debris zones are rescued.
   c. Each edge is traced as one continuous path from top to bottom (dynamic programming), with
      large debris films treated as neutral.
   d. Each row's edge is then moved onto the actual cell front, using faint directional cells and
      shape-validated cells that connect back to the cell sheet.
   e. Safety rules: edges never move further into the sheet than the traced edge, narrow spikes
      (single debris specks) are removed, and no outward bulge beyond the smooth line of the cut.
2. Later image -> shift relative to 0 h (``align``), from the fixed debris pattern on the plate.
3. Later image -> cell-free area inside the aligned 0 h outline (``gap_mask``):
   a pixel counts as a cell only if it is new since 0 h AND looks like an in-focus cell.
   Round blobs sitting on debris are reclassified as debris (``drop_round_debris``).

All pixel-size parameters are tuned for images 2576 px wide; ``batch.py`` resizes every image
to that working width before analysis and converts areas back to the original pixels.
"""
import numpy as np
from scipy import ndimage as ndi
from scipy.signal import fftconvolve
from scipy.ndimage import median_filter, gaussian_filter1d
from skimage.filters import threshold_otsu, sato
from skimage.feature import match_template
from skimage.morphology import remove_small_objects, skeletonize
from skimage.measure import label, regionprops

WORK_WIDTH = 2576          # image width the parameters were tuned on


# ============================================================ basic helpers
def enhance(a, sat=0.001):
    lo, hi = np.percentile(a, [sat / 2, 100 - sat / 2])
    return np.clip((a - lo) / max(hi - lo, 1e-6) * 255, 0, 255)


def bgmap(e):
    """Smooth background brightness (illumination) of an enhanced image."""
    h, w = e.shape
    bg = ndi.median_filter(ndi.zoom(e, 0.125, order=1), size=15)
    bg = ndi.zoom(bg, (h / bg.shape[0], w / bg.shape[1]), order=1)
    return bg[:h, :w]


def kernel(r):
    r2 = int(r * r) + 1
    n = int(np.sqrt(r2 + 1e-9))
    y, x = np.mgrid[-n:n + 1, -n:n + 1]
    k = (x * x + y * y <= r2).astype(float)
    return k / k.sum()


def variance(a, r):
    k = kernel(r)
    p = k.shape[0] // 2
    ap = np.pad(a, p, mode='edge')
    m = fftconvolve(ap, k, 'valid')
    m2 = fftconvolve(ap * ap, k, 'valid')
    return np.clip(m2 - m * m, 0, 255)


def masked_variance(e, valid, r):
    k = kernel(r)
    w = valid.astype(float)
    n = np.maximum(fftconvolve(w, k, 'same'), 1e-3)
    m = fftconvolve(e * w, k, 'same') / n
    v = fftconvolve(e * e * w, k, 'same') / n - m * m
    return np.clip(v, 0, 255)


def largest(m):
    lab, n = ndi.label(m)
    if n == 0:
        return m
    s = ndi.sum(m, lab, range(1, n + 1))
    return lab == (int(np.argmax(s)) + 1)


def robust_poly(y, x, deg=2, k=2.0):
    keep = np.ones_like(x, bool)
    for _ in range(15):
        c = np.polyfit(y[keep], x[keep], deg)
        r = x - np.polyval(c, y)
        s = 1.4826 * np.median(np.abs(r[keep] - np.median(r[keep])))
        new = np.abs(r) < k * max(s, 1)
        if (new == keep).all():
            break
        keep = new
    return np.polyval(c, y)


def shift_img(img, dy, dx, fill=np.nan):
    out = np.full(img.shape, fill, float)
    H, W = img.shape
    out[max(0, dy):H + min(0, dy), max(0, dx):W + min(0, dx)] = \
        img[max(0, -dy):H - max(0, dy), max(0, -dx):W - max(0, dx)]
    return out


def outline_mask(XL, XR, shape, dy=0, dx=0):
    """Mask between left/right edge lines, optionally shifted by (dy, dx); extends edges past the ends."""
    H, W = shape
    yy = np.arange(H)

    def ext(e):
        y0 = yy - dy
        out = np.interp(np.clip(y0, 0, H - 1), yy, e)
        for sel, seg in ((y0 < 0, slice(0, 300)), (y0 > H - 1, slice(H - 300, H))):
            if sel.any():
                a, b = np.polyfit(yy[seg], e[seg], 1)
                out[sel] = a * y0[sel] + b
        return out + dx

    xl, xr = ext(XL), ext(XR)
    xx = np.arange(W)[None, :]
    return (xx >= xl[:, None]) & (xx <= xr[:, None])


# ============================================================ 0 h: coarse outline
def debris_painted(a, s=10, thr=7, grow=8):
    e = enhance(a)
    bg = bgmap(e)
    deb = ndi.binary_dilation(ndi.gaussian_filter(np.clip(bg - e, 0, None), s) > thr, iterations=grow)
    return np.where(deb, bg, e)


def outline_edges(a0, f=0.8):
    """Coarse scratch edges from low texture (debris painted out first)."""
    H, W = a0.shape
    ec = debris_painted(a0)
    hp = ec - ndi.gaussian_filter(ec, 4)
    tex = ndi.gaussian_filter(np.abs(hp), (60, 20))
    m = ndi.binary_fill_holes(tex < threshold_otsu(tex) * f)
    L = largest(m)
    rows = np.where(L.any(1))[0]
    y = rows.astype(float)
    le = np.array([np.where(L[v])[0].min() for v in rows], float)
    re = np.array([np.where(L[v])[0].max() for v in rows], float)
    yy = np.arange(H)

    def robust(x, k=2.0):
        keep = np.ones_like(x, bool)
        for _ in range(10):
            a, b = np.polyfit(y[keep], x[keep], 1)
            r = x - (a * y + b)
            s = 1.4826 * np.median(np.abs(r[keep] - np.median(r[keep])))
            new = np.abs(r) < k * max(s, 1)
            if (new == keep).all():
                break
            keep = new
        return keep

    def edge(x):
        keep = robust(x)
        full = np.interp(yy, y[keep], x[keep])
        return gaussian_filter1d(median_filter(full, size=151, mode='nearest'), 20, mode='nearest')

    return edge(le), edge(re), L


# ============================================================ 0 h: cell maps
def cell_map(a0, sigmas=(1.5, 2.5)):
    """Density of thin elongated ridges (spindle cells); dark blurry debris zeroed."""
    e = enhance(a0)
    f = e - bgmap(e)
    r = np.maximum(sato(f, sigmas=sigmas, black_ridges=False), sato(f, sigmas=sigmas, black_ridges=True))
    dark = ndi.gaussian_filter(np.clip(-f, 0, None), 8) > 6
    r[ndi.binary_dilation(dark, iterations=6)] = 0
    return ndi.gaussian_filter(r, 10)


def normalise(dens, guide):
    """0 = empty-scratch level, 1 = cell-sheet level."""
    core = ndi.binary_erosion(guide, iterations=60)
    far = ~ndi.binary_dilation(guide, iterations=250)
    E = np.percentile(dens[core], 50) if core.any() else np.percentile(dens, 10)
    C = np.percentile(dens[far], 50)
    return np.clip((dens - E) / (C - E), 0, 1.5)


def debris_zone(a0):
    e = enhance(a0)
    f = e - bgmap(e)
    dark = ndi.gaussian_filter(np.clip(-f, 0, None), 8) > 6
    return ndi.binary_dilation(dark, iterations=6)


def film_mask_strict(a0, min_area=40000, dark_t=12):
    """Large, dense clusters of strongly dark blurry spots (debris / marker film)."""
    e = enhance(a0)
    f = e - bgmap(e)
    dark = ndi.gaussian_filter(np.clip(-f, 0, None), 8) > dark_t
    dens = ndi.gaussian_filter(dark.astype(float), 30)
    m = ndi.binary_fill_holes(dens > 0.12)
    lab, n = ndi.label(m)
    if n == 0:
        return m
    s = ndi.sum(m, lab, range(1, n + 1))
    keep = np.isin(lab, 1 + np.where(s >= min_area)[0])
    return ndi.binary_dilation(keep, iterations=20)


def straight_spindles(a0, guide, pct=95.0, min_len=20, min_elong=3.0, max_curve=1.35):
    """Straight, elongated ridge objects (cells); curved arcs (debris rings) rejected."""
    e = enhance(a0)
    f = e - bgmap(e)
    r = np.maximum(sato(f, sigmas=(1.5, 2.5), black_ridges=False), sato(f, sigmas=(1.5, 2.5), black_ridges=True))
    core = ndi.binary_erosion(guide, iterations=80)
    t = np.percentile(r[core], pct)
    lab = label(r > t)
    keep = np.zeros(r.shape, bool)
    for rp in regionprops(lab):
        if rp.area < 15:
            continue
        L = rp.axis_major_length
        Wd = max(rp.axis_minor_length, 1.0)
        if L < min_len or L / Wd < min_elong:
            continue
        sk = skeletonize(lab[rp.slice] == rp.label)
        ys, xs = np.nonzero(sk)
        if len(ys) < 2:
            continue
        chord = np.hypot(np.ptp(ys), np.ptp(xs)) + 1
        if sk.sum() / chord > max_curve:
            continue
        keep[rp.slice] |= (lab[rp.slice] == rp.label)
    return keep


def cell_sheet_v3(a0, guide, close_r=12, grow=6):
    D = normalise(cell_map(a0), guide)
    base = ndi.binary_opening(D > 0.5, iterations=2)
    rescue = ndi.binary_dilation(straight_spindles(a0, guide) & debris_zone(a0), iterations=grow)
    cells = base | rescue
    yy, xx = np.mgrid[-close_r:close_r + 1, -close_r:close_r + 1]
    cells = ndi.binary_closing(cells, structure=xx * xx + yy * yy <= close_r * close_r)
    return ndi.gaussian_filter(cells.astype(float), 4), base, rescue


def clean_cell_map(a0, guide, min_area=150, round_max=2.0):
    D = normalise(cell_map(a0), guide)
    base = ndi.binary_opening(D > 0.5, iterations=2)
    lab = label(base)
    keep = np.zeros_like(base)
    for rp in regionprops(lab):
        elong = rp.axis_major_length / max(rp.axis_minor_length, 1.0)
        if rp.area >= 4 * min_area or (rp.area >= min_area and elong >= round_max):
            keep[rp.slice] |= lab[rp.slice] == rp.label
    rescue = ndi.binary_dilation(straight_spindles(a0, guide) & debris_zone(a0), iterations=4)
    return keep | rescue


def coherence_map(a0, gs=1.5, isg=8):
    e = enhance(a0)
    f = e - bgmap(e)
    f = f - ndi.gaussian_filter(f, 6)
    gx = ndi.gaussian_filter(f, gs, order=(0, 1))
    gy = ndi.gaussian_filter(f, gs, order=(1, 0))
    Jxx = ndi.gaussian_filter(gx * gx, isg)
    Jyy = ndi.gaussian_filter(gy * gy, isg)
    Jxy = ndi.gaussian_filter(gx * gy, isg)
    tr = Jxx + Jyy
    coh = np.sqrt((Jxx - Jyy) ** 2 + 4 * Jxy ** 2) / (tr + 1e-6)
    return coh, tr


def faint_cells(a0, guide, pct=97.0, min_area=200, max_len=180):
    """Faint spindle cells: above-noise texture that runs in one direction; long creases removed."""
    coh, tr = coherence_map(a0)
    de = coh * tr
    core = ndi.binary_erosion(guide, iterations=80)
    T = np.percentile(de[core], pct)
    m = ndi.binary_opening(de > T, iterations=2)
    lab = label(m)
    keep = np.zeros_like(m)
    for rp in regionprops(lab):
        L = rp.axis_major_length
        Wd = max(rp.axis_minor_length, 1.0)
        if rp.area < min_area:
            continue
        if L > max_len and L / Wd > 5:
            continue
        keep[rp.slice] |= lab[rp.slice] == rp.label
    return keep


def shape_cells(a0, guide):
    return (faint_cells(a0, guide) | ndi.binary_dilation(straight_spindles(a0, guide), iterations=4)) \
        & ~film_mask_strict(a0)


# ============================================================ 0 h: edge tracing and refinement
def trace_edge(D, side, lo, hi, win=40, max_step=3, step_pen=0.02):
    """Best continuous top-to-bottom edge path. side=-1 left edge, +1 right edge."""
    H, W = D.shape
    cs = np.cumsum(np.pad(D, ((0, 0), (1, 0))), axis=1)
    xs = np.arange(lo, hi)
    xl = np.clip(xs - win, 0, W)
    xr = np.clip(xs + win, 0, W)
    L = (cs[:, xs] - cs[:, xl]) / np.maximum(xs - xl, 1)
    R = (cs[:, xr] - cs[:, xs]) / np.maximum(xr - xs, 1)
    cost = R + (1 - np.clip(L, 0, 1)) if side < 0 else L + (1 - np.clip(R, 0, 1))
    n = len(xs)
    acc = cost[0].copy()
    back = np.zeros((H, n), np.int16)
    steps = np.arange(-max_step, max_step + 1)
    for y in range(1, H):
        cand = np.full((len(steps), n), np.inf)
        for k, s in enumerate(steps):
            src = np.arange(n) - s
            ok = (src >= 0) & (src < n)
            cand[k, ok] = acc[src[ok]] + step_pen * abs(s)
        kbest = np.argmin(cand, axis=0)
        back[y] = steps[kbest]
        acc = cand[kbest, np.arange(n)] + cost[y]
    path = np.zeros(H, int)
    path[-1] = int(np.argmin(acc))
    for y in range(H - 1, 0, -1):
        path[y - 1] = path[y] - back[y, path[y]]
    return xs[path].astype(float)


def traced_edges(a0, search=260, smooth=8, win=30, max_step=3, step_pen=0.04, out_tol=20, close_r=20):
    GL, GR, _ = outline_edges(a0)
    guide = outline_mask(GL, GR, a0.shape)
    S, _, _ = cell_sheet_v3(a0, guide, close_r=close_r)
    S2 = np.where(film_mask_strict(a0), 0.5, S)
    H, W = a0.shape
    mid = int(np.median((GL + GR) / 2))
    XL = trace_edge(S2, -1, int(max(0, np.median(GL) - search)), mid, win=win, max_step=max_step, step_pen=step_pen)
    XR = trace_edge(S2, +1, mid, int(min(W, np.median(GR) + search)), win=win, max_step=max_step, step_pen=step_pen)
    XL = gaussian_filter1d(XL, smooth)
    XR = gaussian_filter1d(XR, smooth)
    y = np.arange(H, dtype=float)
    fl, fr = robust_poly(y, XL), robust_poly(y, XR)
    XL = gaussian_filter1d(np.maximum(XL, fl - out_tol), 4)
    XR = gaussian_filter1d(np.minimum(XR, fr + out_tol), 4)
    return XL, XR


def _refine(C, XL, XR, out_max=80, in_max=170, link=5, deep=200, med=31, smooth=4):
    """Move each row's edge to the last/first cell that connects back to the cell sheet."""
    H, W = C.shape
    Cd = ndi.binary_dilation(C, iterations=link)
    lab, _ = ndi.label(Cd)
    xx = np.arange(W)[None, :]
    deep_out = (xx < (XL - deep)[:, None]) | (xx > (XR + deep)[:, None])
    ids = np.unique(lab[deep_out & Cd])
    ids = ids[ids > 0]
    A = np.isin(lab, ids) & C
    nl = np.empty(H)
    nr = np.empty(H)
    for r in range(H):
        a_, b_ = int(max(0, XL[r] - out_max)), int(min(W, XL[r] + in_max))
        hit = np.where(A[r, a_:b_])[0]
        nl[r] = a_ + hit[-1] + 1 if hit.size else a_
        a_, b_ = int(max(0, XR[r] - in_max)), int(min(W, XR[r] + out_max))
        hit = np.where(A[r, a_:b_])[0]
        nr[r] = a_ + hit[0] - 1 if hit.size else b_ - 1
    nl = gaussian_filter1d(median_filter(nl, med, mode='nearest'), smooth)
    nr = gaussian_filter1d(median_filter(nr, med, mode='nearest'), smooth)
    return nl, nr


def outline_0h(a0):
    """Final 0 h scratch outline. Returns (left_edge, right_edge, mask), edges as x per row."""
    H = a0.shape[0]
    y = np.arange(H, dtype=float)
    XL, XR = traced_edges(a0)
    GL, GR, _ = outline_edges(a0)
    guide = outline_mask(GL, GR, a0.shape)
    film = film_mask_strict(a0)
    C1 = (clean_cell_map(a0, guide) | faint_cells(a0, guide)) & ~film     # sheet-connected incl. faint cells
    l1, r1 = _refine(C1, XL, XR, link=5)
    l2, r2 = _refine(shape_cells(a0, guide), XL, XR, link=14)              # shape-validated cells only
    nl = gaussian_filter1d(np.maximum(l1, l2), 3)                          # tightest to the cells
    nr = gaussian_filter1d(np.minimum(r1, r2), 3)
    nl = np.maximum(nl, XL - 10)                                           # never further into the sheet
    nr = np.minimum(nr, XR + 10)
    nl = median_filter(nl, 61, mode='nearest')                             # remove narrow spikes
    nr = median_filter(nr, 61, mode='nearest')
    fl, fr = robust_poly(y, nl), robust_poly(y, nr)                        # no outward bulge past the cut
    nl = gaussian_filter1d(np.maximum(nl, fl - 25), 4)
    nr = gaussian_filter1d(np.minimum(nr, fr + 25), 4)
    return nl, nr, outline_mask(nl, nr, a0.shape)


# ============================================================ later time points
def align(a0, at, min_score=0.5, max_shift=300):
    """Shift (dy, dx) that moves the 0 h field onto image ``at``, matched on the fixed debris pattern
    in a 3 x 4 grid of regions. Returns (dy, dx), number of confident regions, best score."""
    blob = lambda a: ndi.gaussian_filter(a, 6) - ndi.gaussian_filter(a, 50)
    b0, bt = blob(a0), blob(at)
    H, W = a0.shape
    hs, ws = 420, 520
    found = []
    for y0 in np.linspace(60, H - hs - 60, 3).astype(int):
        for x0 in np.linspace(60, W - ws - 60, 4).astype(int):
            t = b0[y0:y0 + hs, x0:x0 + ws]
            ya, yb = max(0, y0 - max_shift), min(H, y0 + hs + max_shift)
            xa, xb = max(0, x0 - max_shift), min(W, x0 + ws + max_shift)
            r = match_template(bt[ya:yb, xa:xb], t)
            iy, ix = np.unravel_index(np.argmax(r), r.shape)
            found.append((ya + iy - y0, xa + ix - x0, r.max()))
    found = np.array(found)
    good = found[found[:, 2] >= min_score]
    n_good = len(good)
    if n_good == 0:
        good = found[np.argsort(found[:, 2])[-1:]]
    dy, dx = np.median(good[:, 0]), np.median(good[:, 1])
    spread = float(np.abs(good[:, :2] - [dy, dx]).max()) if len(good) > 1 else 0.0
    return (int(round(dy)), int(round(dx))), n_good, float(found[:, 2].max()), spread


def flat(a):
    e = enhance(a)
    return e - bgmap(e)


def gap_mask(at, a0, band, dy, dx, t_app=15, mult=2.0, frac_cal=0.3, min_gap=300):
    """Cell-free area inside ``band`` at a later time point."""
    H, W = at.shape
    r = 20 * W / 3072
    ft = flat(at)
    f0 = shift_img(flat(a0), dy, dx)
    ok = band & ~np.isnan(f0)
    A = np.vstack([f0[ok], np.ones(ok.sum())]).T
    coef, *_ = np.linalg.lstsq(A, ft[ok], rcond=None)
    res = ft - (coef[0] * np.nan_to_num(f0) + coef[1])
    res[np.isnan(f0)] = ft[np.isnan(f0)]
    vres = variance(res, r)
    new = vres > np.percentile(vres[band], 10) * mult                # changed since 0 h
    e = enhance(at)
    bg = bgmap(e)
    low = ndi.gaussian_filter(np.abs(ndi.gaussian_filter(e, 4) - bg), 6)
    sharp = ndi.gaussian_filter(np.abs(e - ndi.gaussian_filter(e, 1.5)), 6)
    far = ~ndi.binary_dilation(band, iterations=150)
    empty = np.median(sharp[band & (low < 2)])
    cellsharp = np.median(sharp[far & (low > 2)])
    st = empty + frac_cal * (cellsharp - empty)
    deb = ndi.binary_dilation((low > 4) & (sharp < st), iterations=6)
    k = kernel(r)
    frac = fftconvolve((~deb).astype(float), k, 'same')
    v = masked_variance(e, ~deb, r)
    looks = ((v > t_app) & (frac >= 0.2)) | ((frac < 0.2) & (ndi.gaussian_filter(sharp, 4) >= st))
    return remove_small_objects(band & ~(new & looks), max_size=min_gap)


def debris_map_0h(a0, band0):
    e0 = enhance(a0)
    bg0 = bgmap(e0)
    low0 = ndi.gaussian_filter(np.abs(ndi.gaussian_filter(e0, 4) - bg0), 6)
    sharp0 = ndi.gaussian_filter(np.abs(e0 - ndi.gaussian_filter(e0, 1.5)), 6)
    dark0 = ndi.gaussian_filter(np.clip(bg0 - e0, 0, None), 10) > 7
    far = ~ndi.binary_dilation(band0, iterations=300)
    em = np.median(sharp0[band0 & (low0 < 2)])
    ce = np.median(sharp0[far & (low0 > 2)])
    return ndi.binary_dilation(dark0 | ((low0 > 4) & (sharp0 < em + 0.3 * (ce - em))), iterations=8)


def drop_round_debris(op, band, deb_t, W, at=None, max_area=None, ecc_max=0.85, overlap_min=0.3):
    """Round, small 'cell' blobs sitting on debris are debris -> counted as open."""
    if max_area is None:
        max_area = 3000 * (W / 2576) ** 2
    cells = band & ~op
    if at is not None:
        e = enhance(at)
        bg = bgmap(e)
        deb_t = deb_t | ndi.binary_dilation(ndi.gaussian_filter(np.clip(bg - e, 0, None), 10) > 7, iterations=10)
    lab = label(cells)
    out = op.copy()
    for rp in regionprops(lab):
        sl = lab[rp.slice] == rp.label
        fp = regionprops(ndi.binary_fill_holes(sl).astype(int))[0]
        if fp.area <= max_area and fp.eccentricity < ecc_max and deb_t[rp.slice][sl].mean() >= overlap_min:
            out[rp.slice][sl] = True
    return out


def measure_later(a0, band0_edges, deb0, at):
    """Align ``at`` to ``a0`` and measure its cell-free area inside the aligned 0 h outline.
    Returns dict with mask, outline and alignment info."""
    XL, XR = band0_edges
    (dy, dx), n_good, best, spread = align(a0, at)
    band = outline_mask(XL, XR, at.shape, dy, dx)
    op = gap_mask(at, a0, band, dy, dx)
    debt = shift_img(deb0.astype(float), dy, dx, fill=0) > 0.5
    op = drop_round_debris(op, band, debt, at.shape[1], at=at)
    return dict(open=op, band=band, shift=(dy, dx), n_confident=n_good, best_score=best, spread=spread)
