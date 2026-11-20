'''Shrink contact photos so vCards / archives stay small.

Uses macOS's built-in `sips` (no third-party dependencies), batching many files per call.
Contact photos only need to be a few hundred pixels across, so big originals (some are 3600x3600 px,
2+ MB) are scaled down and re-encoded as JPEG (or PNG when they have transparency).
'''

import re
import shutil
import tempfile
from pathlib import Path
from subprocess import run

from lib import sniff_image_type

DEFAULT_MAX_PX = 512
DEFAULT_QUALITY = 80
REENCODE_ABOVE_BYTES = 150_000  # an image already within max_px is still re-encoded if it is bigger than this
EXTS = {'jpeg': 'jpg', 'png': 'png', 'gif': 'gif', 'tiff': 'tif', 'bmp': 'bmp'}
CHUNK = 200  # files per sips call, to stay well under the OS arg-length limit


def sips_available():
    return shutil.which('sips') is not None


def _sips(args, files):
    '''Run sips over files in batches, yielding each call's stdout. One malformed image can make sips abort
    its whole batch (it even crashes on some inputs), so a failed batch is retried file by file and only the
    bad files are skipped (they simply have no output / no dimensions, and callers keep the original).'''
    for i in range(0, len(files), CHUNK):
        chunk = files[i:i + CHUNK]
        r = run(['sips', *args, *map(str, chunk)], capture_output=True, text=True)
        if r.returncode == 0:
            yield r.stdout
            continue
        for f in chunk:
            r = run(['sips', *args, str(f)], capture_output=True, text=True)
            if r.returncode == 0:
                yield r.stdout


def _dims(files):
    '''{path: (width, height, has_alpha)} for the given image files.'''
    out, cur = {}, None
    for stdout in _sips(['-g', 'pixelWidth', '-g', 'pixelHeight', '-g', 'hasAlpha'], files):
        for line in stdout.splitlines():
            if not line.startswith(' '):
                cur = Path(line.strip())
                out[cur] = {}
            elif cur is not None and ':' in line:
                k, v = (s.strip() for s in line.split(':', 1))
                out[cur][k] = v
    res = {}
    for p, d in out.items():
        try:
            res[p] = (int(d['pixelWidth']), int(d['pixelHeight']), d.get('hasAlpha') == 'yes')
        except (KeyError, ValueError):
            pass  # sips couldn't read it; leave that photo untouched
    return res


def shrink_photos(photos, max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
    '''Shrink {key: (image_type, bytes)} photos. Returns {key: (image_type, bytes, scale)} for every key,
    where scale = new pixel width / old pixel width (1.0 if the photo was not resized, or kept as is).
    A photo is replaced only when the result is smaller. Never raises for a single bad image: on any
    problem the original is returned. If max_px is 0 or sips is missing, everything is returned unchanged.
    '''
    result = {k: (t, b, 1.0) for k, (t, b) in photos.items()}
    if not photos or not max_px:
        return result
    if not sips_available():
        print('WARNING: `sips` not found (needs macOS); photos will not be shrunk.')
        return result

    with tempfile.TemporaryDirectory() as tmp:
        # resolve(): on macOS /var -> /private/var, and sips echoes the resolved path, which we match on.
        src_dir, out_dir = Path(tmp).resolve() / 'src', Path(tmp).resolve() / 'out'
        src_dir.mkdir()
        out_dir.mkdir()
        files = {}
        for i, (k, (t, b)) in enumerate(photos.items()):
            f = src_dir / f'{i}.{EXTS.get(t, "img")}'
            f.write_bytes(b)
            files[k] = f
        try:
            dims = _dims(list(files.values()))
        except RuntimeError as e:
            print(f'WARNING: could not read photo sizes ({e}); photos will not be shrunk.')
            return result

        # Group by the sips arguments they need, so each group is one batched call.
        groups = {}
        for k, f in files.items():
            if f not in dims:
                continue
            w, h, alpha = dims[f]
            resize = max(w, h) > max_px
            if not resize and len(photos[k][1]) <= REENCODE_ABOVE_BYTES:
                continue  # already small
            fmt = ['-s', 'format', 'png'] if alpha else ['-s', 'format', 'jpeg', '-s', 'formatOptions', str(quality)]
            args = (['-Z', str(max_px)] if resize else []) + fmt
            groups.setdefault(tuple(args), []).append(f)

        for args, fs in groups.items():
            try:
                list(_sips([*args, '--out', str(out_dir)], fs))
            except RuntimeError as e:
                print(f'WARNING: sips failed on a batch of {len(fs)} photos ({e}); keeping originals for them.')
                for f in fs:
                    for stale in out_dir.glob(f'{f.stem}.*'):
                        stale.unlink()

        # sips changes the output suffix when the format changes (0.png -> 0.jpg), so find outputs by stem.
        by_stem = {p.stem: p for p in out_dir.iterdir() if p.is_file()}
        outs = {k: by_stem[f.stem] for k, f in files.items() if f.stem in by_stem}
        new_dims = _dims(list(outs.values())) if outs else {}
        for k, o in outs.items():
            data = o.read_bytes()
            t = sniff_image_type(data[:32])
            if t and len(data) < len(photos[k][1]) and o in new_dims:
                result[k] = (t, data, new_dims[o][0] / dims[files[k]][0])
    return result


# sips can't be asked for a crop here; Apple's ZCROPRECT is "{{x, y}, {w, h}}" in original-pixel coordinates.
_RECT_RE = re.compile(r'^\{\{\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\}\s*,\s*\{\s*(-?[\d.]+)\s*,\s*(-?[\d.]+)\s*\}\}$')


def scale_crop_rect(rect, scale):
    '''Scale an Apple crop-rect string like "{{111, 0}, {289, 289}}" by `scale`, returning the same format.
    Returns the input unchanged if it can't be parsed or scale is 1.'''
    if not isinstance(rect, str) or scale == 1.0:
        return rect
    m = _RECT_RE.match(rect.strip())
    if not m:
        return rect
    x, y, w, h = (round(float(v) * scale) for v in m.groups())
    return f'{{{{{x}, {y}}}, {{{w}, {h}}}}}'
