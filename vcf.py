'''vCard 3.0 export, alphabetized and split into files of at most N contacts.

Core fields (names, phones, emails, urls, addresses) come from the cleaned contact dicts built by main.py.
Everything else (middle name, nickname, job title, birthday, notes, social profiles, IM, related names,
dates, photos, ...) is read straight from the AddressBook sqlite dbs by parse_extras(), one table at a
time, so multi-valued data is not lost by the big join in lib.parse_abcddb().
'''

import base64
import datetime
import re
import unicodedata
from pathlib import Path

from lib import query, table_names, column_names, sniff_image_type

APPLE_EPOCH = datetime.datetime(2001, 1, 1)  # Core Data timestamps are seconds since this
UUID_RE = re.compile(r'^[0-9A-Fa-f-]{36}$')
PHOTO_TYPES = {'jpeg': 'JPEG', 'png': 'PNG', 'gif': 'GIF', 'tiff': 'TIFF', 'bmp': 'BMP'}

# ZABCDRECORD column -> key in the per-contact "extras" dict
SCALAR_COLUMNS = {
    'ZMIDDLENAME': 'middle',
    'ZSUFFIX': 'suffix',
    'ZTITLE': 'prefix',          # name prefix, eg "Dr."
    'ZNICKNAME': 'nickname',
    'ZJOBTITLE': 'jobtitle',
    'ZDEPARTMENT': 'department',
    'ZMAIDENNAME': 'maiden',
    'ZPHONETICFIRSTNAME': 'phonetic_first',
    'ZPHONETICMIDDLENAME': 'phonetic_middle',
    'ZPHONETICLASTNAME': 'phonetic_last',
    'ZPHONETICORGANIZATION': 'phonetic_org',
}


##################################################
# Reading the extra data out of a .abcddb
##################################################

def _rows(db, table, owner_cols, fields):
    '''Yield (owner_value, {field: value}) for each row of table. Only constant column names from the
    arguments are ever put into the SQL, and only if the db really has them; missing columns are omitted.'''
    if table not in table_names(db):
        return
    cols = column_names(table, db)
    owner = next((c for c in owner_cols if c in cols), None)
    have = [f for f in fields if f in cols]
    if owner is None or not have:
        return
    rows, _ = query(db, f"SELECT {owner}, {', '.join(have)} FROM {table}")
    for r in rows:
        yield r[0], dict(zip(have, r[1:]))


def _text(v):
    return v.strip() if isinstance(v, str) and v.strip() else None


def _date_from_timestamp(ts, year):
    '''(year-or-None, month, day) from a Core Data timestamp plus the separately-stored year.'''
    try:
        dt = APPLE_EPOCH + datetime.timedelta(seconds=float(ts))
    except (TypeError, ValueError, OverflowError):
        return None
    return (int(year) if year else None, dt.month, dt.day)


def decode_photo(blob, external_dir):
    '''Decode Apple's ZIMAGEDATA / ZTHUMBNAILIMAGEDATA blob into (image_type, bytes), or None.
    First byte 0x01 = image bytes follow inline; 0x02 = NUL-terminated UUID of a file in external_dir.
    '''
    if not blob:
        return None
    blob = bytes(blob)
    if blob[0] == 1:
        data = blob[1:]
    elif blob[0] == 2:
        name = blob[1:].split(b'\0')[0].decode('ascii', 'ignore')
        if not UUID_RE.match(name):
            return None
        path = Path(external_dir) / name
        if not path.is_file():
            return None
        data = path.read_bytes()
    else:
        data = blob
    t = sniff_image_type(data[:32])
    return (t, data) if t else None


def parse_extras(db):
    '''Return {uid: extras} for the people in the given AddressBook sqlite db.
    extras has any of: middle, suffix, prefix, nickname, jobtitle, department, maiden, phonetic_*,
    bday=(year|None,m,d), company=True, photo=(type, bytes), and lists notes, social, im, related, dates, caluri.
    '''
    support = Path(db).parent / '.AddressBook-v22_SUPPORT' / '_EXTERNAL_DATA'
    pk2uid, extras = {}, {}

    record_fields = list(SCALAR_COLUMNS) + ['ZUNIQUEID', 'ZBIRTHDAY', 'ZBIRTHDAYYEAR', 'ZDISPLAYFLAGS',
                                            'ZIMAGEDATA', 'ZTHUMBNAILIMAGEDATA']
    for pk, r in _rows(db, 'ZABCDRECORD', ('Z_PK',), record_fields):
        uid = r.get('ZUNIQUEID')
        if not uid or not uid.endswith(':ABPerson'):
            continue
        uid = uid.replace(':ABPerson', '')
        pk2uid[pk] = uid
        d = extras.setdefault(uid, {})
        for col, key in SCALAR_COLUMNS.items():
            if _text(r.get(col)):
                d[key] = _text(r[col])
        if r.get('ZBIRTHDAY') is not None:
            bd = _date_from_timestamp(r['ZBIRTHDAY'], r.get('ZBIRTHDAYYEAR'))
            if bd:
                d['bday'] = bd
        if isinstance(r.get('ZDISPLAYFLAGS'), int) and (r['ZDISPLAYFLAGS'] & 7) == 1:
            d['company'] = True  # Contacts' "Company" checkbox
        for col in ('ZIMAGEDATA', 'ZTHUMBNAILIMAGEDATA'):
            p = decode_photo(r.get(col), support)
            if p and len(p[1]) > len(d.get('photo', (None, b''))[1]):
                d['photo'] = p  # keep the largest

    def add(owner, key, value):
        uid = pk2uid.get(owner)
        if uid is not None:
            extras[uid].setdefault(key, []).append(value)

    for o, r in _rows(db, 'ZABCDNOTE', ('ZCONTACT',), ['ZTEXT']):
        if _text(r.get('ZTEXT')):
            add(o, 'notes', r['ZTEXT'].strip())

    for o, r in _rows(db, 'ZABCDSOCIALPROFILE', ('ZOWNER',), ['ZSERVICENAME', 'ZUSERNAME', 'ZURLSTRING']):
        if _text(r.get('ZUSERNAME')) or _text(r.get('ZURLSTRING')):
            add(o, 'social', (_text(r.get('ZSERVICENAME')) or 'other', _text(r.get('ZUSERNAME')) or '', _text(r.get('ZURLSTRING')) or ''))

    for o, r in _rows(db, 'ZABCDMESSAGINGADDRESS', ('ZOWNER',), ['ZSERVICE', 'ZADDRESS', 'ZLABEL']):
        if _text(r.get('ZADDRESS')):
            add(o, 'im', (r.get('ZSERVICE'), r['ZADDRESS'].strip(), r.get('ZLABEL') or ''))

    for o, r in _rows(db, 'ZABCDRELATEDNAME', ('ZOWNER',), ['ZLABEL', 'ZNAME']):
        if _text(r.get('ZNAME')):
            add(o, 'related', (r.get('ZLABEL') or '', r['ZNAME'].strip()))

    for o, r in _rows(db, 'ZABCDCONTACTDATE', ('ZOWNER',), ['ZLABEL', 'ZDATE', 'ZDATEYEAR']):
        if r.get('ZDATE') is not None:
            dt = _date_from_timestamp(r['ZDATE'], r.get('ZDATEYEAR'))
            if dt:
                add(o, 'dates', (r.get('ZLABEL') or '', dt))

    for o, r in _rows(db, 'ZABCDCALENDARURI', ('ZOWNER',), ['ZLABEL', 'ZURL']):
        if _text(r.get('ZURL')):
            add(o, 'caluri', (r.get('ZLABEL') or '', r['ZURL'].strip()))

    return extras


def _dedupe(items):
    seen, res = set(), []
    for x in items:
        k = repr(x)
        if k not in seen:
            seen.add(k)
            res.append(x)
    return res


def _dedupe_by(labeled_values, keyfn):
    '''Drop (label, value) pairs whose normalized value was already seen; the first label wins.'''
    seen, res = set(), []
    for lab, val in labeled_values:
        k = keyfn(val or '')
        if k not in seen:
            seen.add(k)
            res.append((lab, val))
    return res


def merge_extras(list_of_extras):
    '''Combine the {uid: extras} dicts from several dbs (a person can appear in more than one).'''
    out = {}
    for ex in list_of_extras:
        for uid, d in ex.items():
            m = out.setdefault(uid, {})
            for k, v in d.items():
                if isinstance(v, list):
                    m[k] = _dedupe(m.get(k, []) + v)
                elif k == 'photo':
                    if len(v[1]) > len(m.get('photo', (None, b''))[1]):
                        m['photo'] = v
                else:
                    m.setdefault(k, v)  # first one wins
    return out


##################################################
# vCard rendering
##################################################

def _vesc(s):
    '''Escape a vCard text value.'''
    s = str(s).replace('\r\n', '\n').replace('\r', '\n')
    return s.replace('\\', '\\\\').replace('\n', '\\n').replace(';', '\\;').replace(',', '\\,')


def _vtype(label):
    '''Turn a label like 'Mobile' or '_$!<Work>!$_' into a safe vCard TYPE value ('' if none).'''
    return re.sub(r'[^A-Za-z0-9-]', '', (label or '').replace('_$!<', '').replace('>!$_', ''))


def _tp(label):
    t = _vtype(label)
    return f';TYPE={t}' if t else ''


def _fold(line):
    '''Fold a vCard line to <=75 octets (continuation lines start with a space), never splitting a character.'''
    b = line.encode('utf-8')
    if len(b) <= 75:
        return line
    out, first = [], True
    while b:
        chunk = b[:75 if first else 74]
        while True:
            try:
                chunk.decode('utf-8')
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        out.append(('' if first else ' ') + chunk.decode('utf-8'))
        b = b[len(chunk):]
        first = False
    return '\r\n'.join(out)


def _fmt_date(d):
    '''(year|None, m, d) -> ('1604-MM-DD', needs_omit_year_param) using Apple's 1604 convention for "no year".'''
    y, m, dd = d
    return (f'{y:04d}-{m:02d}-{dd:02d}', False) if y else (f'1604-{m:02d}-{dd:02d}', True)


def contact_to_vcard(c, x=None):
    '''One contact dict (+ its extras dict) as a vCard 3.0 string. Returns (text, has_photo).'''
    x = x or {}
    first, last, org = c.get('first', ''), c.get('last', ''), c.get('organization', '')
    phones = _dedupe_by(c.get('phone', []), lambda v: _phone_key(v) or v.strip().lower())
    emails = _dedupe_by(c.get('email', []), lambda v: v.strip().lower())
    urls = _dedupe_by(c.get('url', []), lambda v: v.strip().lower())
    addrs = _dedupe(c.get('address', []))

    fn = ' '.join(p for p in (x.get('prefix'), first, x.get('middle'), last, x.get('suffix')) if p) or org \
        or (emails[0][1] if emails else '') or (phones[0][1] if phones else '') or c['uid']

    L = ['BEGIN:VCARD', 'VERSION:3.0',
         f"N:{_vesc(last)};{_vesc(first)};{_vesc(x.get('middle', ''))};{_vesc(x.get('prefix', ''))};{_vesc(x.get('suffix', ''))}",
         f'FN:{_vesc(fn)}']
    if x.get('nickname'):
        L.append(f"NICKNAME:{_vesc(x['nickname'])}")
    if x.get('maiden'):
        L.append(f"X-MAIDENNAME:{_vesc(x['maiden'])}")
    for key, prop in (('phonetic_first', 'X-PHONETIC-FIRST-NAME'), ('phonetic_middle', 'X-PHONETIC-MIDDLE-NAME'),
                      ('phonetic_last', 'X-PHONETIC-LAST-NAME'), ('phonetic_org', 'X-PHONETIC-ORG')):
        if x.get(key):
            L.append(f'{prop}:{_vesc(x[key])}')
    if org or x.get('department'):
        L.append(f"ORG:{_vesc(org)}" + (f";{_vesc(x['department'])}" if x.get('department') else ''))
    if x.get('jobtitle'):
        L.append(f"TITLE:{_vesc(x['jobtitle'])}")
    if x.get('company'):
        L.append('X-ABShowAs:COMPANY')
    if x.get('bday'):
        s, omit = _fmt_date(x['bday'])
        L.append(f"BDAY{';X-APPLE-OMIT-YEAR=1604' if omit else ''}:{s}")

    for lab, val in phones:
        L.append(f'TEL{_tp(lab)}:{_vesc(val)}')
    for lab, val in emails:
        L.append(f'EMAIL{_tp(lab)}:{_vesc(val)}')
    for lab, val in urls:
        L.append(f'URL{_tp(lab)}:{_vesc(val)}')
    for lab, a in addrs:
        parts = [a.get(k, '') for k in ('street', 'city', 'state', 'zip', 'country')]
        L.append(f"ADR{_tp(lab)}:;;" + ';'.join(_vesc(p) for p in parts))

    for service, user, url in x.get('social', []):
        stype = re.sub(r'[^A-Za-z0-9_-]', '', service).lower() or 'other'
        xuser = f';x-user={re.sub(r"[^A-Za-z0-9_.@-]", "", user)}' if user else ''
        L.append(f"X-SOCIALPROFILE;type={stype}{xuser}:{_vesc(url or 'x-apple:' + user)}")
    for service, addr, lab in x.get('im', []):
        # ZSERVICE is an unnamed integer enum in the db, so record it as "Service<N>" rather than guess a name.
        value = addr if '://' in addr else 'im:' + addr
        L.append(f"IMPP;X-SERVICE-TYPE=Service{service if service is not None else 0}{_tp(lab)}:{_vesc(value)}")
    for lab, url in x.get('caluri', []):
        L.append(f'CALURI{_tp(lab)}:{_vesc(url)}')

    n = 0  # Apple's "itemN." groups tie a value to a custom label
    for lab, name in x.get('related', []):
        n += 1
        L += [f'item{n}.X-ABRELATEDNAMES:{_vesc(name)}', f'item{n}.X-ABLabel:{_vesc(lab or "_$!<Other>!$_")}']
    for lab, d in x.get('dates', []):
        n += 1
        s, omit = _fmt_date(d)
        L += [f"item{n}.X-ABDATE{';X-APPLE-OMIT-YEAR=1604' if omit else ''}:{s}", f'item{n}.X-ABLabel:{_vesc(lab or "_$!<Other>!$_")}']

    if x.get('notes'):
        L.append(f"NOTE:{_vesc(chr(10).join(x['notes']))}")

    has_photo = False
    photo = x.get('photo')
    if photo and photo[0] in PHOTO_TYPES:
        L.append(f"PHOTO;ENCODING=b;TYPE={PHOTO_TYPES[photo[0]]}:" + base64.b64encode(photo[1]).decode('ascii'))
        has_photo = True

    L.append(f"UID:{_vesc(c['uid'])}")
    L.append('END:VCARD')
    return '\r\n'.join(_fold(l) for l in L) + '\r\n', has_photo


##################################################
# Alphabetizing and writing
##################################################

def _norm(s):
    '''Case- and accent-insensitive form for sorting.'''
    return unicodedata.normalize('NFKD', s or '').encode('ascii', 'ignore').decode('ascii').casefold() or (s or '').casefold()


def sort_key(c):
    '''Strict sort by last name, then first name, then organization (case- and accent-insensitive).
    Contacts whose last name starts with a letter come first, then last names starting with a digit or
    symbol, then contacts with no last name at all (ordered by first name, then organization).'''
    last, first, org = _norm(c.get('last', '')), _norm(c.get('first', '')), _norm(c.get('organization', ''))
    group = 2 if not last else (0 if last[0].isalpha() else 1)
    return (group, last, first, org, c['uid'])


LIST_FIELDS = ('phone', 'email', 'url', 'address')


def _phone_key(v):
    d = re.sub(r'\D', '', v or '')
    return d[-10:] if len(d) >= 7 else ''  # compare on the last 10 digits so +1 / country prefixes don't matter


def _match_keys(c):
    '''The identifiers two same-named cards can be merged on: lowercased emails and digit-only phones.'''
    keys = {('e', v.strip().lower()) for _, v in c.get('email', []) if v and v.strip()}
    keys |= {('p', _phone_key(v)) for _, v in c.get('phone', []) if _phone_key(v)}
    return keys


def _merge_contacts(group):
    '''Merge cards (already sorted, so the first is the base): union the list fields, first non-empty scalar wins.'''
    m = {k: (list(v) if isinstance(v, list) else v) for k, v in group[0].items()}
    for c in group[1:]:
        for k, v in c.items():
            if k in LIST_FIELDS or isinstance(v, list):
                m[k] = _dedupe(m.get(k, []) + list(v))
            elif not m.get(k):
                m[k] = v
    return m


def dedupe_contacts(sorted_contacts, extras):
    '''Merge cards with the same first name, last name and a shared email or phone.
    Expects contacts already sorted with sort_key, so cards with the same name are adjacent.
    Cards with no person name (organization only) must also share the organization.
    Returns (merged contacts, extras dict for them, number of cards removed).
    '''
    name_key = lambda c: (_norm(c.get('first', '')), _norm(c.get('last', '')),
                          '' if (c.get('first') or c.get('last')) else _norm(c.get('organization', '')))
    out, new_extras, removed = [], dict(extras), 0
    i = 0
    while i < len(sorted_contacts):
        j = i
        while j < len(sorted_contacts) and name_key(sorted_contacts[j]) == name_key(sorted_contacts[i]):
            j += 1
        block = sorted_contacts[i:j]
        i = j
        # union-find over the block: cards sharing any email/phone end up in one cluster
        parent = list(range(len(block)))
        def find(a):
            while parent[a] != a:
                parent[a] = parent[parent[a]]
                a = parent[a]
            return a
        owner = {}
        for idx, c in enumerate(block):
            for k in _match_keys(c):
                if k in owner:
                    parent[find(idx)] = find(owner[k])
                else:
                    owner[k] = idx
        clusters = {}
        for idx in range(len(block)):
            clusters.setdefault(find(idx), []).append(block[idx])
        for group in clusters.values():  # dicts keep first-seen order, so alphabetical order is preserved
            if len(group) == 1:
                out.append(group[0])
                continue
            merged = _merge_contacts(group)
            ex = merge_extras([{'x': extras[c['uid']]} for c in group if c['uid'] in extras]).get('x')
            if ex:
                new_extras[merged['uid']] = ex
            out.append(merged)
            removed += len(group) - 1
    return out, new_extras, removed


def export_vcf(contacts, outdir, max_per_file=500, extras=None):
    '''Write contacts, alphabetized, to outdir/contacts-001-of-NNN.vcf, ... with at most max_per_file each.
    Removes old contacts-*.vcf in outdir first so stale files from earlier runs don't linger.
    Returns the list of files written.
    '''
    extras = extras or {}
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for old in outdir.glob('contacts-*.vcf'):
        old.unlink()
    # Drop contacts with no person name and no organization (they'd otherwise show up as just an email or phone).
    named = [c for c in contacts if any((c.get(k) or '').strip() for k in ('first', 'last', 'organization'))]
    dropped = len(contacts) - len(named)
    cs = sorted(named, key=sort_key)
    cs, extras, merged_away = dedupe_contacts(cs, extras)
    cs.sort(key=sort_key)  # merging can fill in fields (eg organization) that change a card's sort position
    nfiles = max(1, -(-len(cs) // max_per_file))
    width = max(3, len(str(nfiles)))
    files, photos, skipped_photos = [], 0, 0
    for i in range(nfiles):
        chunk = cs[i * max_per_file:(i + 1) * max_per_file]
        f = outdir / f'contacts-{i + 1:0{width}d}-of-{nfiles:0{width}d}.vcf'
        with open(f, 'w', encoding='utf-8', newline='') as fh:
            for c in chunk:
                x = extras.get(c['uid'])
                text, has_photo = contact_to_vcard(c, x)
                photos += has_photo
                skipped_photos += bool(x and x.get('photo')) and not has_photo
                fh.write(text)
        print(f'Wrote {len(chunk)} contacts to {f}')
        files.append(f)
    print(f'VCF: dropped {dropped} contacts with no name or organization; merged away {merged_away} duplicate cards.')
    print(f'VCF: {len(cs)} contacts in {nfiles} file(s); {photos} photos embedded; {skipped_photos} photos skipped (unsupported image type).')
    return files
