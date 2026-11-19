"""Batch processing of a plate folder: find wells and time points, run the analysis, write outputs.

Plate folder layout (one folder per plate, no subfolders needed)::

    Plate1_2026-10-29/
        A1_0h.tif   A1_6h.tif   A1_24h.tif
        A2_0h.tif   A2_6h.tif   A2_24h.tif
        B1-1_0h.tif ...                      (second field in well B1)
        settings.txt                         (optional, e.g. "um_per_pixel = 1.0")

File names are ``<Well>_<Time>h.<ext>``; the separator before the time can be ``_``, ``-`` or a space.
"""
import os
import re
import sys
import time
import traceback
import datetime as _dt
from concurrent.futures import ProcessPoolExecutor, as_completed

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi

from . import analysis as an

VERSION = '1.0.0'
IMAGE_EXT = {'.tif', '.tiff', '.png', '.jpg', '.jpeg', '.bmp'}
NAME_RE = re.compile(r'^(?P<well>.+?)[ _\-](?P<t>\d+(?:\.\d+)?)\s*h$', re.IGNORECASE)
RESULTS_PREFIX = 'Results_'


# ============================================================ folder parsing
def natural_key(s):
    return [int(p) if p.isdigit() else p.lower() for p in re.split(r'(\d+)', s)]


def fmt_time(t):
    return str(int(t)) if float(t).is_integer() else str(t)


def read_settings(folder):
    s = {}
    p = os.path.join(folder, 'settings.txt')
    if os.path.isfile(p):
        with open(p, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.split('#')[0].strip()
                if '=' in line:
                    k, v = [x.strip() for x in line.split('=', 1)]
                    s[k.lower()] = v
    out = {}
    if 'um_per_pixel' in s:
        try:
            out['um_per_pixel'] = float(s['um_per_pixel'])
        except ValueError:
            out['settings_error'] = 'um_per_pixel in settings.txt is not a number: %r' % s['um_per_pixel']
    if 'workers' in s:
        try:
            out['workers'] = max(1, int(s['workers']))
        except ValueError:
            pass
    return out


def scan_folder(folder):
    """Return (wells, problems, ignored). wells = {well: {time: filename}}."""
    wells, problems, ignored = {}, [], []
    for name in sorted(os.listdir(folder), key=natural_key):
        path = os.path.join(folder, name)
        if os.path.isdir(path) or name.startswith('.') or name.lower() == 'settings.txt':
            continue
        stem, ext = os.path.splitext(name)
        if ext.lower() not in IMAGE_EXT:
            ignored.append(name)
            continue
        m = NAME_RE.match(stem.strip())
        if not m:
            problems.append(('?', 'Cannot read time point from "%s" (expected e.g. A1_24h%s)' % (name, ext)))
            continue
        well, t = m.group('well').strip(), float(m.group('t'))
        if t in wells.setdefault(well, {}):
            problems.append((well, 'Duplicate time point %sh: "%s" and "%s"' % (fmt_time(t), wells[well][t], name)))
            continue
        wells[well][t] = name
    for well in list(wells):
        if 0.0 not in wells[well]:
            problems.append((well, 'No 0h image for well %s; well skipped' % well))
            del wells[well]
    return dict(sorted(wells.items(), key=lambda kv: natural_key(kv[0]))), problems, ignored


# ============================================================ image I/O
def load_gray(path):
    """Grayscale float image. 8-bit images use the same conversion the method was validated on."""
    try:
        im = Image.open(path)
        im.seek(0)
        if im.mode in ('I;16', 'I;16B', 'I;16L', 'I', 'F'):
            a = np.asarray(im).astype(float)
        else:
            a = np.asarray(im.convert('L')).astype(float)
    except Exception:
        import tifffile
        a = tifffile.imread(path).astype(float)
        if a.ndim == 3:
            a = a[0] if a.shape[0] < 5 else a.mean(axis=2)
    if a.max() > 255:                                    # 16-bit etc.: rescale to 0-255
        lo, hi = np.percentile(a, [0.01, 99.99])
        a = np.clip((a - lo) / max(hi - lo, 1e-6) * 255, 0, 255)
    return a


def to_work(a):
    """Resize to the working width the parameters were tuned on. Returns (image, scale)."""
    H, W = a.shape
    if W == an.WORK_WIDTH:
        return a, 1.0
    s = an.WORK_WIDTH / W
    # LANCZOS keeps fine cell detail; it matched the validated 2576 px results most closely in testing
    im = Image.fromarray(a.astype(np.float32), mode='F').resize((an.WORK_WIDTH, max(1, round(H * s))), Image.LANCZOS)
    return np.asarray(im).astype(float), s


def to_orig(mask, shape):
    H, W = shape
    if mask.shape == (H, W):
        return mask
    im = Image.fromarray(mask.astype(np.uint8) * 255).resize((W, H), Image.NEAREST)
    return np.asarray(im) > 127


def preview_rgb(path):
    im = Image.open(path)
    if im.mode not in ('L', 'RGB'):
        a = load_gray(path)
        im = Image.fromarray(a.astype(np.uint8))
    return im.convert('RGB')


def overlay(gray, band, open_mask):
    g = np.stack([gray / max(gray.max(), 1) * 235] * 3, -1).astype(np.uint8)
    g[open_mask] = (0.55 * g[open_mask] + [0, 80, 115]).astype(np.uint8)
    w = max(2, round(4 * gray.shape[1] / 2576))
    g[band ^ ndi.binary_erosion(band, iterations=w)] = [230, 40, 40]
    return Image.fromarray(g)


def _font(size, bold=False):
    names = ['DejaVuSans-Bold.ttf' if bold else 'DejaVuSans.ttf', 'arialbd.ttf' if bold else 'arial.ttf',
             'Arial Bold.ttf' if bold else 'Arial.ttf']
    for n in names:
        try:
            return ImageFont.truetype(n, size)
        except Exception:
            continue
    return ImageFont.load_default()


def well_figure(well, items, path):
    """Originals on top, overlays below. items = [(time, filename, original_rgb, overlay_img, label)]."""
    PW = 700
    PH = round(PW * items[0][2].height / items[0][2].width)
    n = len(items)
    out = Image.new('RGB', (n * PW + (n - 1) * 16, 2 * PH + 190), 'white')
    d = ImageDraw.Draw(out)
    B, R = _font(24, True), _font(19)
    d.text((4, 4), 'Well %s: original images (top) and cell-free area in blue (bottom)' % well, fill='black', font=B)
    for i, (t, fn, raw, ov, lab) in enumerate(items):
        x = i * (PW + 16)
        d.text((x + 2, 38), '%sh  %s' % (fmt_time(t), fn), fill='black', font=R)
        out.paste(raw.resize((PW, PH), Image.LANCZOS), (x, 66))
        d.text((x + 2, PH + 74), '%sh  (%s)' % (fmt_time(t), lab), fill='black', font=B)
        out.paste(ov.resize((PW, PH), Image.LANCZOS), (x, PH + 106))
    d.text((4, 2 * PH + 120), 'Blue = cell-free. Red = 0 h scratch outline aligned onto each image.', fill=(60, 60, 60), font=R)
    out.save(path, quality=90)


def save_roi(XL, XR, path, step=5):
    try:
        import roifile
    except ImportError:
        return False
    H = len(XL)
    ys = list(range(0, H, step)) + [H - 1]
    pts = [(float(XL[y]), float(y)) for y in ys] + [(float(XR[y]), float(y)) for y in reversed(ys)]
    roi = roifile.ImagejRoi.frompoints(np.array(pts))
    roi.roitype = roifile.ROI_TYPE.POLYGON
    roi.tofile(path)
    return True


def edges_of(mask):
    """Left/right edge x per row of a band mask (for ROI export of shifted outlines)."""
    H = mask.shape[0]
    xl = np.full(H, np.nan)
    xr = np.full(H, np.nan)
    for y in range(H):
        xs = np.flatnonzero(mask[y])
        if xs.size:
            xl[y], xr[y] = xs[0], xs[-1]
    ok = ~np.isnan(xl)
    if not ok.any():
        return None
    yy = np.arange(H)
    return np.interp(yy, yy[ok], xl[ok]), np.interp(yy, yy[ok], xr[ok])


# ============================================================ manual corrections
CORRECTIONS_DIR = 'corrections'


def find_correction(folder, well):
    """A hand-corrected 0 h outline for ``well``: corrections/<well>_0h.roi (or _0h_outline.roi)."""
    d = os.path.join(folder, CORRECTIONS_DIR)
    for name in ('%s_0h.roi' % well, '%s_0h_outline.roi' % well):
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return p
    return None


def load_correction(path, shape_orig, shape_work):
    """ImageJ ROI (any closed shape, original-image pixels) -> (XL, XR, mask) at working size."""
    import roifile
    from skimage.draw import polygon
    roi = roifile.ImagejRoi.fromfile(path)
    if isinstance(roi, list):
        roi = roi[0]
    pts = np.asarray(roi.coordinates(), float)
    H, W = shape_orig
    rr, cc = polygon(pts[:, 1], pts[:, 0], shape=(H, W))
    m = np.zeros((H, W), bool)
    m[rr, cc] = True
    if not m.any():
        raise ValueError('outline in %s is empty or outside the image' % os.path.basename(path))
    Hw, Ww = shape_work
    mw = np.asarray(Image.fromarray(m.astype(np.uint8) * 255).resize((Ww, Hw), Image.NEAREST)) > 127
    xl, xr = edges_of(mw)
    return xl, xr, mw


# ============================================================ one well
def process_well(well, times, folder, outdir):
    """Analyse one well. Returns a dict with per-image rows and well-level info."""
    t_start = time.time()
    rows, notes = [], []
    t_sorted = sorted(times)
    p0 = os.path.join(folder, times[0.0])
    g0 = load_gray(p0)
    a0, s0 = to_work(g0)
    corr = find_correction(folder, well)
    if corr:                                              # hand-corrected outline wins
        XL, XR, band0 = load_correction(corr, g0.shape, a0.shape)
        method0 = '0h scratch outline: manual correction (%s)' % os.path.basename(corr)
    else:
        XL, XR, band0 = an.outline_0h(a0)
        method0 = '0h scratch outline (reference, 100% cell-free)'
    rows0 = band0.any(1)                                  # rows the outline covers
    film = an.film_mask_strict(a0)
    film_frac = float((film & band0).sum() / max(band0.sum(), 1))
    deb0 = an.debris_map_0h(a0, band0)
    width_px = float(np.mean(XR - XL) / s0)
    band0_o = to_orig(band0, g0.shape)
    items = []
    ov0 = overlay(g0, band0_o, band0_o)
    ov0.save(os.path.join(outdir, 'overlays', '%s_0h_overlay.jpg' % well), quality=90)
    e0 = edges_of(band0_o)
    if e0 is not None:
        save_roi(e0[0], e0[1], os.path.join(outdir, 'outlines', '%s_0h_outline.roi' % well))
    A0 = int(band0_o.sum())
    qc0 = an.outline_check(XL, XR, a0.shape[1])
    contrast = an.scratch_columns(an.texture_map(a0))[2]
    edge0 = band0 ^ ndi.binary_erosion(band0, iterations=40)
    film_edge = float((film & edge0).sum() / max(edge0.sum(), 1))
    if film_edge >= 0.10:
        qc0.append('Outline edge runs under debris/artefact for %.0f%% of its length; check the edge there' % (100 * film_edge))
    if contrast < 0.15:
        qc0.append('Scratch hard to tell from cell sheet (texture contrast %.0f%%)' % (100 * contrast))
    rows.append(dict(well=well, time=0.0, filename=times[0.0], width=g0.shape[1], height=g0.shape[0],
                     scratch=A0, method=method0, qc=qc0))
    items.append((0.0, times[0.0], preview_rgb(p0), ov0, 'reference outline'))
    prev_open = A0
    for t in t_sorted[1:]:
        pt = os.path.join(folder, times[t])
        gt = load_gray(pt)
        qc = []
        if gt.shape != g0.shape:
            qc.append('Image size differs from 0h (%dx%d vs %dx%d)' % (gt.shape[1], gt.shape[0], g0.shape[1], g0.shape[0]))
            gt = np.asarray(Image.fromarray(gt.astype(np.float32), mode='F').resize((g0.shape[1], g0.shape[0]), Image.LANCZOS)).astype(float)
        at, _ = to_work(gt)
        r = an.measure_later(a0, (XL, XR), deb0, at)
        if not rows0.all():                               # outline doesn't span the image: keep its rows only
            keep = an.shift_img(np.broadcast_to(rows0[:, None], band0.shape).astype(float), *r['shift'], fill=0) > 0.5
            r['band'] &= keep
            r['open'] &= keep
        op_o = to_orig(r['open'], gt.shape)
        band_o = to_orig(r['band'], gt.shape)
        At = int(op_o.sum())
        if r['n_confident'] < 2 or r['spread'] > 6:
            qc.append('Low alignment confidence (%d of 12 regions matched)' % r['n_confident'])
        if At > prev_open + 0.02 * A0:
            qc.append('Closure decreased vs previous time point')
        prev_open = At
        ov = overlay(gt, band_o, op_o)
        ov.save(os.path.join(outdir, 'overlays', '%s_%sh_overlay.jpg' % (well, fmt_time(t))), quality=90)
        e = edges_of(band_o)
        if e is not None:
            save_roi(e[0], e[1], os.path.join(outdir, 'outlines', '%s_%sh_outline.roi' % (well, fmt_time(t))))
        dy, dx = r['shift']
        notes.append('%sh shift vs 0h: dy=%d dx=%d px (working size), %d/12 regions matched' % (fmt_time(t), dy, dx, r['n_confident']))
        rows.append(dict(well=well, time=t, filename=times[t], width=gt.shape[1], height=gt.shape[0],
                         scratch=At, method='cell-free gaps inside aligned 0h outline', qc=qc))
        items.append((t, times[t], preview_rgb(pt), ov, '%.1f%% closed' % (100 * (1 - At / max(A0, 1)))))
    if film_frac > 0.25:
        rows[0]['qc'].append('Large debris area (%.0f%% of the scratch)' % (100 * film_frac))
    well_figure(well, items, os.path.join(outdir, 'figures', '%s_figure.jpg' % well))
    return dict(well=well, rows=rows, width_px=width_px, notes=notes, seconds=time.time() - t_start)


def _worker(args):
    well, times, folder, outdir = args
    try:
        return process_well(well, times, folder, outdir)
    except Exception as ex:
        return dict(well=well, error='%s: %s' % (type(ex).__name__, ex), trace=traceback.format_exc(), times=times)


# ============================================================ Excel
def write_excel(path, plate, results, settings, problems, run_info):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
    from openpyxl.utils import get_column_letter
    F = 'Arial'
    hdr_fill = PatternFill('solid', start_color='1F4E79')
    thin = Side(style='thin', color='BFBFBF')
    um = settings.get('um_per_pixel')
    wb = Workbook()
    ws = wb.active
    ws.title = 'Results'
    cols = ['plate', 'well', 'time_point_h', 'filename', 'method', 'image_width_px', 'image_height_px',
            'total_area_px2', 'scratch_area_px2', 'scratch_area_percent', 'closure_vs_0h_percent']
    if um:
        cols.append('scratch_area_um2')
    cols.append('qc_flag')
    for c, h in enumerate(cols, 1):
        cell = ws.cell(row=1, column=c, value=h)
        cell.font = Font(name=F, bold=True, color='FFFFFF')
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)
    r = 2
    first_row = {}
    all_times = set()
    for res in results:
        if 'error' in res:
            for t, fn in sorted(res['times'].items()):
                vals = [plate, res['well'], float(t), fn, 'not analysed'] + [None] * (len(cols) - 6) + ['ERROR: ' + res['error']]
                for c, v in enumerate(vals, 1):
                    ws.cell(row=r, column=c, value=v).font = Font(name=F, color='C00000')
                r += 1
            continue
        r0 = r
        first_row[res['well']] = r0
        for row in res['rows']:
            all_times.add(row['time'])
            t = row['time']
            vals = {1: plate, 2: row['well'], 3: int(t) if float(t).is_integer() else t, 4: row['filename'],
                    5: row['method'], 6: row['width'], 7: row['height'], 8: '=F%d*G%d' % (r, r),
                    9: row['scratch'], 10: '=I%d/H%d' % (r, r), 11: '=1-I%d/I$%d' % (r, r0)}
            c = 12
            if um:
                vals[c] = "=I%d*Settings!$B$5^2" % r
                c += 1
            vals[c] = '; '.join(row['qc']) if row['qc'] else 'OK'
            for col, v in vals.items():
                cell = ws.cell(row=r, column=col, value=v)
                cell.font = Font(name=F, color='0000FF' if col in (6, 7, 9) else '000000')
                cell.border = Border(bottom=thin)
            for col in (6, 7, 8, 9):
                ws.cell(row=r, column=col).number_format = '#,##0'
            if um:
                ws.cell(row=r, column=12).number_format = '#,##0'
            ws.cell(row=r, column=10).number_format = '0.00%'
            ws.cell(row=r, column=11).number_format = '0.00%'
            if row['qc']:
                ws.cell(row=r, column=len(cols)).font = Font(name=F, bold=True, color='C00000')
            r += 1
    widths = [16, 9, 12, 26, 44, 15, 16, 16, 17, 19, 21] + ([18] if um else []) + [44]
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = 'A2'
    ws.auto_filter.ref = 'A1:%s%d' % (get_column_letter(len(cols)), max(r - 1, 1))

    # Summary: closure per well x time
    sm = wb.create_sheet('Summary')
    times = sorted(all_times)
    heads = ['well', '0h scratch area (px2)'] + ['closure at %sh' % fmt_time(t) for t in times if t != 0] + ['qc']
    for c, h in enumerate(heads, 1):
        cell = sm.cell(row=1, column=c, value=h)
        cell.font = Font(name=F, bold=True, color='FFFFFF')
        cell.fill = hdr_fill
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
    rr = 2
    for res in results:
        if 'error' in res:
            sm.cell(row=rr, column=1, value=res['well']).font = Font(name=F)
            sm.cell(row=rr, column=len(heads), value='ERROR').font = Font(name=F, color='C00000')
            rr += 1
            continue
        r0 = first_row[res['well']]
        sm.cell(row=rr, column=1, value=res['well']).font = Font(name=F)
        c = sm.cell(row=rr, column=2, value='=Results!I%d' % r0)
        c.font = Font(name=F, color='008000')
        c.number_format = '#,##0'
        tmap = {row['time']: r0 + i for i, row in enumerate(res['rows'])}
        for j, t in enumerate([t for t in times if t != 0], 3):
            if t in tmap:
                c = sm.cell(row=rr, column=j, value='=Results!K%d' % tmap[t])
                c.font = Font(name=F, color='008000')
                c.number_format = '0.0%'
        flags = sorted({q for row in res['rows'] for q in row['qc']})
        sm.cell(row=rr, column=len(heads), value='; '.join(flags) if flags else 'OK').font = \
            Font(name=F, color='C00000' if flags else '000000')
        rr += 1
    for i in range(1, len(heads) + 1):
        sm.column_dimensions[get_column_letter(i)].width = 14 if i > 1 else 10
    sm.column_dimensions[get_column_letter(len(heads))].width = 44
    sm.row_dimensions[1].height = 32

    # Settings / notes
    st = wb.create_sheet('Settings')
    info = [('Plate folder', plate), ('Run started', run_info['started']), ('Tool version', VERSION),
            ('Working width (px)', an.WORK_WIDTH), ('um per pixel', um if um else 'not set (add settings.txt)'),
            ('Wells analysed', run_info['n_ok']), ('Wells with errors', run_info['n_err'])]
    for i, (k, v) in enumerate(info, 1):
        st.cell(row=i, column=1, value=k).font = Font(name=F, bold=True)
        cell = st.cell(row=i, column=2, value=v)
        cell.font = Font(name=F, color='0000FF' if k == 'um per pixel' and um else '000000')
    n = len(info) + 2
    notes = ['Notes',
             'Blue numbers come from the image analysis; black numbers are calculated by formula.',
             'scratch_area_px2 at 0h = area inside the 0h scratch outline (treated as 100% cell-free).',
             'At later time points = cell-free area still inside that outline, aligned to each image.',
             'closure_vs_0h_percent = 1 - (scratch area at this time / scratch area at 0h of the same well).',
             'qc_flag: anything other than OK should be checked in figures/ before results are reported.',
             'Areas are in the pixels of the original images.']
    for i, t in enumerate(notes):
        st.cell(row=n + i, column=1, value=t).font = Font(name=F, bold=(i == 0), italic=(i > 0), size=10)
    if problems:
        n += len(notes) + 1
        st.cell(row=n, column=1, value='Folder problems (skipped)').font = Font(name=F, bold=True, color='C00000')
        for i, (w, msg) in enumerate(problems, 1):
            st.cell(row=n + i, column=1, value=msg).font = Font(name=F, color='C00000')
    st.column_dimensions['A'].width = 40
    st.column_dimensions['B'].width = 36
    wb.save(path)


# ============================================================ run
def default_workers():
    n = os.cpu_count() or 1
    return 1 if n <= 2 else min(3, n - 1)


def run(folder, workers=None, log=print):
    folder = os.path.abspath(folder)
    plate = os.path.basename(folder.rstrip(os.sep))
    started = _dt.datetime.now()
    settings = read_settings(folder)
    workers = workers or settings.get('workers') or default_workers()
    wells, problems, ignored = scan_folder(folder)
    if settings.get('settings_error'):
        problems.append(('-', settings['settings_error']))

    log('Plate folder: %s' % folder)
    log('Found %d well(s):' % len(wells))
    for w, ts in wells.items():
        log('  %-8s %s' % (w, ', '.join('%sh' % fmt_time(t) for t in sorted(ts))))
    for _, msg in problems:
        log('  PROBLEM: ' + msg)
    if ignored:
        log('  Ignored (not images): ' + ', '.join(ignored))
    corrected = [w for w in wells if find_correction(folder, w)]
    if corrected:
        log('  Manual 0h outline (corrections folder): ' + ', '.join(corrected))
    if not wells:
        log('\nNo wells to analyse. Check the file names (e.g. A1_0h.tif, A1_24h.tif).')
        return None

    outdir = os.path.join(folder, RESULTS_PREFIX + started.strftime('%Y-%m-%d_%H%M'))
    for sub in ('overlays', 'figures', 'outlines'):
        os.makedirs(os.path.join(outdir, sub), exist_ok=True)
    n_images = sum(len(t) for t in wells.values())
    log('\nAnalysing %d images in %d wells using %d worker(s). This takes about 30-60 seconds per image.\n'
        % (n_images, len(wells), workers))

    tasks = [(w, ts, folder, outdir) for w, ts in wells.items()]
    results = {}
    t0 = time.time()
    if workers == 1:
        for i, a in enumerate(tasks, 1):
            res = _worker(a)
            results[res['well']] = res
            _report(res, i, len(tasks), t0, log)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_worker, a) for a in tasks]
            for i, f in enumerate(as_completed(futs), 1):
                res = f.result()
                results[res['well']] = res
                _report(res, i, len(tasks), t0, log)
    ordered = [results[w] for w in wells]

    ok = [r for r in ordered if 'error' not in r]
    if len(ok) >= 3:                                   # plate-level check: unusual scratch width
        med = float(np.median([r['width_px'] for r in ok]))
        for r in ok:
            if r['width_px'] < 0.6 * med or r['width_px'] > 1.6 * med:
                r['rows'][0]['qc'].append('Unusual outline width (%.0f px vs plate median %.0f px)' % (r['width_px'], med))

    xlsx = os.path.join(outdir, 'wound_results.xlsx')
    write_excel(xlsx, plate, ordered, settings, problems,
                dict(started=started.strftime('%Y-%m-%d %H:%M'), n_ok=len(ok), n_err=len(ordered) - len(ok)))

    with open(os.path.join(outdir, 'run_log.txt'), 'w', encoding='utf-8') as fh:
        fh.write('Wound healing batch analysis v%s\nPlate folder: %s\nStarted: %s\nFinished: %s\nWorkers: %d\n'
                 % (VERSION, folder, started, _dt.datetime.now(), workers))
        fh.write('um per pixel: %s\n\n' % settings.get('um_per_pixel', 'not set'))
        for _, msg in problems:
            fh.write('PROBLEM: %s\n' % msg)
        for r in ordered:
            if 'error' in r:
                fh.write('\n%s: ERROR %s\n%s\n' % (r['well'], r['error'], r['trace']))
            else:
                fh.write('\n%s: %.0f s\n' % (r['well'], r['seconds']))
                for n in r['notes']:
                    fh.write('  %s\n' % n)
                for row in r['rows']:
                    if row['qc']:
                        fh.write('  %sh QC: %s\n' % (fmt_time(row['time']), '; '.join(row['qc'])))
    n_flag = sum(1 for r in ok for row in r['rows'] if row['qc'])
    log('\nDone in %.0f min. %d well(s) analysed, %d with errors, %d image(s) flagged for checking.'
        % ((time.time() - t0) / 60, len(ok), len(ordered) - len(ok), n_flag))
    log('Results: %s' % outdir)
    return outdir


def _report(res, i, n, t0, log):
    el = time.time() - t0
    eta = el / i * (n - i)
    if 'error' in res:
        log('[%d/%d] %s: ERROR %s' % (i, n, res['well'], res['error']))
    else:
        cl = ', '.join('%sh %.1f%%' % (fmt_time(r['time']), 100 * (1 - r['scratch'] / max(res['rows'][0]['scratch'], 1)))
                       for r in res['rows'][1:])
        log('[%d/%d] %s done (%.0f s)%s   ~%.0f min left' % (i, n, res['well'], res['seconds'],
                                                             ('  closure: ' + cl) if cl else '', eta / 60))
