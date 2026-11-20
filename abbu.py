'''Write a copy of an .abbu Contacts archive whose contact photos have been shrunk.

Only photos change: no contact is merged, edited or deleted. The source archive is never modified.
Photos live in ZABCDRECORD.ZIMAGEDATA, either inline (first byte 0x01) or as a file under
<source>/.AddressBook-v22_SUPPORT/_EXTERNAL_DATA/<UUID> (first byte 0x02 + the UUID). Older archives
also have loose files in Images/ dirs.
'''

import hashlib
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from lib import sniff_image_type
from photos import shrink_photos, scale_crop_rect, DEFAULT_MAX_PX, DEFAULT_QUALITY
from vcf import locate_photo


def _shrink_db(db, max_px, quality):
    '''Shrink the photos (full image and thumbnail) referenced by one .abcddb and its _EXTERNAL_DATA files, in place.
    Returns (photos changed, bytes before, bytes after).'''
    support = Path(db).parent / '.AddressBook-v22_SUPPORT' / '_EXTERNAL_DATA'
    with closing(sqlite3.connect(db)) as con:
        cols = [r[1] for r in con.execute('PRAGMA table_info(ZABCDRECORD)')]
        if 'ZIMAGEDATA' not in cols:
            return 0, 0, 0
        has_thumb = 'ZTHUMBNAILIMAGEDATA' in cols
        rows = con.execute(f"SELECT Z_PK, ZIMAGEDATA, {'ZTHUMBNAILIMAGEDATA' if has_thumb else 'NULL'}, ZCROPRECT, ZIMAGEHASH "
                           'FROM ZABCDRECORD WHERE ZIMAGEDATA IS NOT NULL OR '
                           f"{'ZTHUMBNAILIMAGEDATA IS NOT NULL' if has_thumb else '0'}").fetchall()
        # Each row has up to two photos: the full image (+ its crop rect and hash) and the display thumbnail.
        located, photos = {}, {}
        for pk, blob, thumb, rect, h in rows:
            for which, b in (('image', blob), ('thumb', thumb)):
                loc = locate_photo(b, support)
                t = sniff_image_type(loc[1][:32]) if loc else None
                if t:
                    located[pk, which] = loc
                    photos[pk, which] = (t, loc[1])
        shrunk = shrink_photos(photos, max_px, quality)

        changed = before = after = 0
        written = set()  # an external file can be shared by several rows; write it once
        for pk, blob, thumb, rect, h in rows:
            sets, vals = [], []
            for which, b, col in (('image', blob, 'ZIMAGEDATA'), ('thumb', thumb, 'ZTHUMBNAILIMAGEDATA')):
                if (pk, which) not in shrunk:
                    continue
                kind, old, path = located[pk, which]
                t, new, scale = shrunk[pk, which]
                if new is old:
                    continue  # left as is
                if kind == 'external':
                    if path not in written:
                        path.write_bytes(new)
                        written.add(path)
                    new_blob = b  # still points at the same file
                elif kind == 'inline':
                    new_blob = b'\x01' + new
                else:
                    new_blob = new
                sets.append(f'{col} = ?')
                vals.append(new_blob)
                if which == 'image':
                    # The crop rect is in image pixels, so scale it with the image. ZIMAGEHASH is the md5 of
                    # the image bytes in most rows; keep it in step where that holds.
                    new_hash = hashlib.md5(new).digest() if h is not None and bytes(h) == hashlib.md5(old).digest() else h
                    sets += ['ZCROPRECT = ?', 'ZIMAGEHASH = ?']
                    vals += [scale_crop_rect(rect, scale), new_hash]
                changed += 1
                before += len(old)
                after += len(new)
            if sets:
                con.execute(f"UPDATE ZABCDRECORD SET {', '.join(sets)} WHERE Z_PK = ?", (*vals, pk))
        con.commit()
        con.isolation_level = None  # VACUUM can't run inside a transaction
        con.execute('VACUUM')
        check = con.execute('PRAGMA integrity_check').fetchone()[0]
        if check != 'ok':
            raise RuntimeError(f'{db}: integrity_check failed after shrinking photos: {check}')
    return changed, before, after


def _shrink_loose_images(root, max_px, quality):
    '''Shrink legacy image files in Images/ dirs under root, in place. Returns (changed, before, after).'''
    files = [f for f in Path(root).glob('**/Images/*') if f.is_file()]
    photos = {}
    for f in files:
        data = f.read_bytes()
        t = sniff_image_type(data[:32])
        if t:
            photos[f] = (t, data)
    shrunk = shrink_photos(photos, max_px, quality)
    changed = before = after = 0
    for f, (t, new, _scale) in shrunk.items():
        if new is not photos[f][1]:
            f.write_bytes(new)
            changed += 1
            before += len(photos[f][1])
            after += len(new)
    return changed, before, after


def shrink_abbu(src, dst, max_px=DEFAULT_MAX_PX, quality=DEFAULT_QUALITY):
    '''Copy the .abbu dir src to dst (replacing any existing dst), then shrink every contact photo in the copy.'''
    src, dst = Path(src).resolve(), Path(dst).resolve()
    assert src.is_dir(), f'{src} is not a directory'
    assert dst != src and src not in dst.parents and dst not in src.parents, 'dst must be outside of src (and vice versa)'
    assert dst.suffix == '.abbu', f'Refusing to write to {dst}: expected the name to end with ".abbu"'
    print(f'START: copy {src}\n   to {dst} and shrink photos to <= {max_px}px.')
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    total = [0, 0, 0]
    for db in sorted(dst.glob('**/*.abcddb')):
        c, b, a = _shrink_db(db, max_px, quality)
        print(f'  {db.relative_to(dst)}: {c} photos, {b / 1e6:.1f} MB -> {a / 1e6:.1f} MB')
        total = [x + y for x, y in zip(total, (c, b, a))]
    c, b, a = _shrink_loose_images(dst, max_px, quality)
    total = [x + y for x, y in zip(total, (c, b, a))]
    size = lambda p: sum(f.stat().st_size for f in p.glob('**/*') if f.is_file())
    print(f'DONE: shrank {total[0]} photos ({total[1] / 1e6:.1f} MB -> {total[2] / 1e6:.1f} MB). '
          f'Archive size: {size(src) / 1e6:.1f} MB -> {size(dst) / 1e6:.1f} MB.')
    return dst
