from pprint import pprint as pp
from pprint import pformat
from pathlib import Path
import plistlib, re, shutil
from lib import get_file_info, parse_abcddb, gather, merge_dicts, duplicate_freeQ, dict_subsetQ, export

print("DEPRECATION WARNING: I use the stdlib's imghdr module to identify the image type of files. Deprecated in Py 3.11, removed in Py 3.13. More info: https://docs.python.org/3/library/imghdr.html")
import imghdr

# Parse a Mac Address Book file (.abbu) into JSON. See README.md for details.

def main():
    dirs = list(Path('./in/').glob('*.abbu'))
    assert len(dirs)==1, 'Expected exactly 1 .abbu file in the \'in\' dir!'

    BASE_DIR = dirs[0].absolute()
    assert BASE_DIR.is_dir()

    OUT_DIR = Path('./out').absolute()
    assert OUT_DIR.is_dir()

    OUT_IMS_DIR = OUT_DIR / 'ims'
    if not OUT_IMS_DIR.exists():
        OUT_IMS_DIR.mkdir()

    OUT_ORPHAN_IMS_DIR = OUT_IMS_DIR / 'orphans'
    if not OUT_ORPHAN_IMS_DIR.exists():
        OUT_ORPHAN_IMS_DIR.mkdir()

    print(f'Parsing this ".abbu" mac address book:\n{BASE_DIR}')

    assert (BASE_DIR / 'Metadata').is_dir(), f'Expected given dir "{BASE_DIR}" to have dir "Metadata"!'
    assert (BASE_DIR / 'Sources').is_dir(), f'Expected given dir "{BASE_DIR}" to have dir "Sources"!'
    assert (BASE_DIR / 'AddressBook-v22.abcddb').is_file(), f'Expected given dir "{BASE_DIR}" to have file "AddressBook-v22.abcddb"!'
    assert get_file_info(BASE_DIR / 'AddressBook-v22.abcddb').startswith('SQLite 3.x database'), f'''Expected file "{BASE_DIR / 'AddressBook-v22.abcddb'}" to be a SQLite db!'''

    ps = load_people(BASE_DIR)
    ims = load_image_files(BASE_DIR)
    cs = load_contacts(BASE_DIR)
    ps = clean_people(ps)
    cs = clean_contacts(cs)
    verify_people_are_subset_of_contacts(ps,cs)
    orphaned_ims, cs = merge_images_into_contacts(ims,cs)
    actually_copy_and_rename_image_files(cs, OUT_IMS_DIR)
    actually_copy_and_rename_ORPHANED_image_files(orphaned_ims, OUT_ORPHAN_IMS_DIR)
    export(cs,OUT_DIR / 'contacts.json')

    print('bye!!')


def load_people(base_dir : Path):
    print('START: PEOPLE (.abcdp files)')
    ps = []
    for f in base_dir.glob('**/*.abcdp'):
        d = plistlib.load(open(f,'rb'))
        if 'UID' not in d: 
            raise ValueError(f"ERROR: No UID in file\n{f}\nDict:\n{pformat(d,indent=4)}\n""")
        if not d['UID'].endswith(':ABPerson'): 
            raise ValueError(f"""ERROR: Expected UID '{d['UID']}' to end with ':ABPerson' from this dict:\n{pformat(d,indent=4)}\nfrom file:\n{f}""")
        ps.append(d)
    print(f"Done parsing {len(ps)} .abcdp people files into variable 'ps'.")
    if len(ps)>0:
        print('Example:')
        pp(ps[0])

    return ps

def load_image_files(base_dir : Path):
    # Image, stored w/ or w/o file extension, in Images dir
    print('START: IMAGES (any file in any Images/ dir)')
    ims = [{  'path': f,
              'info': get_file_info(f), 
              'image type':imghdr.what(f),
              'base name':f.stem
            } for f in base_dir.glob('**/Images/*') if f.is_file()]
    print(f"Done parsing {len(ims)} images from Images directory(s) into variable 'ims'!")
    if len(ims)>0:
        print("Example:")
        pp(ims[0])

    return ims

def load_contacts(base_dir : Path):
    print('START: DATABASES (.abcddb dirs)')
    cs = []
    fs = list(base_dir.glob('**/*.abcddb')) # Address book, stored as sqlite3 db
    for f in fs:
        ds = parse_abcddb(f) # Each dict is a row from the abcddb's sqlite db query.
        for d in ds:
            if 'ZABCDRECORD.ZUNIQUEID' not in d:
                print(f"Warning: Skipping dict w/ no 'ZABCDRECORD.ZUNIQUEID':\n{pformat(d,indent=4)}")
                continue
            if not d['ZABCDRECORD.ZUNIQUEID'].endswith(':ABPerson'):
                print(f"Info: Expected record's 'ZABCDRECORD.ZUNIQUEID' to end with ':ABPerson', skipping:\n{pformat(d,indent=4)}")
                continue
            cs.append(d)
    print(f"Done parsing {len(cs)} contacts from {len(fs)} .abcddb SQLite databases, into variable 'cs'!")
    if len(cs)>0:
        print("Example:")
        pp(cs[0])

    return cs

def clean_people(ps):
    print(f'START: Clean {len(ps)} people.')

    for p in ps:
        # Delete annoying data.
        #
        [   p.pop(k) 
            for k in [ 'ABPropertyTypes' 
                     , 'ABPersonFlags' 
                     , 'Modification' 
                     , 'Creation' 
                     , 'syncStatus'
                     , 'externalCollectionPath'
                     , 'externalFilename'
                     , 'externalHash'
                     , 'externalModificationTag'
                     , 'externalUUID'
                     ] 
            if k in p
        ]
        [ p.pop(k) for k in [k for k in p if k.startswith('com.apple')] ]  # double list-comp avoids 'dict changed during iteration' error.

        # Convert 
        #     "Phone": {
        #         "identifiers": [
        #             "BEAB044C-7514-4CC7-849F-E710C11537C1", ...
        #         ],
        #         "labels": [
        #             "_$!<Mobile>!$_", ...
        #         ],
        #         "primary": "BEAB044C-7514-4CC7-849F-E710C11537C1",
        #         "values": [
        #             "+1231231234", ...
        #         ]
        #     },
        # to                 
        #     "Phone": [ ('Mobile', '+1231231234'), ... ]
        #
        for k in ["Phone" , "Email" , "Address" , "URLs"]:
            if k in p:
                labs = [lab.replace('_$!<','').replace('>!$_','') for lab in p[k]['labels']]
                vals = p[k]['values']
                p[k] = list(zip(labs,vals))

    # Lowercase all keys.
    ps = [ {k.lower(): v for k,v in p.items()} for p in ps ]

    # Rename key 'urls' to 'url'.
    for p in ps:
        if 'urls' in p:
            p['url'] = p['urls']
            p.pop('urls')

    # Lowercase keys in 'address' dicts, eg make it like this:
    #
    # {'address': [('Work',
    #               {'city': 'Cupertino',
    #                'country': 'United States',
    #                'country code': 'us',
    #                'state': 'CA',
    #                'street': '1 Infinite Loop',
    #                'zip': '95014'})]
    #
    for p in ps:
        if 'address' in p:
            p['address'] = [ \
                            ( lab, 
                              { k.lower().replace('countrycode','country code') \
                                : \
                                v \
                               for k,v in addr.items()
                              }
                            ) \
                            for lab,addr in p['address']
                            ]

    # Remove :ABPerson suffix on UIDs.
    #
    for p in ps:
        p['uid'] = p['uid'].replace(':ABPerson','')

    # Discard k/v pairs w/ empty vals.
    #
    for p in ps:
        ks = list(p.keys())
        for k in ks:
            if not p[k]:
                p.pop(k)

    # Check that UID's are unique.
    assert duplicate_freeQ(ps,lambda p: p['uid'])

    print(f'END: Clean {len(ps)} people.')
    return ps



def clean_contacts(cs):
    print(f'START: Clean {len(cs)} contacts.')
    assert all('ZABCDRECORD.ZUNIQUEID' in c for c in cs), f"Very weird: all contact dicts should have the key 'ZABCDRECORD.ZUNIQUEID'."

    keys_to_delete = [s.strip() for s in str.splitlines('''
        ZABCDCONTACTINDEX.Z21_CONTACT
        ZABCDCONTACTINDEX.Z22_CONTACT
        ZABCDCONTACTINDEX.ZCONTACT
        ZABCDCONTACTINDEX.ZSTRINGFORINDEXING
        ZABCDCONTACTINDEX.Z_ENT
        ZABCDCONTACTINDEX.Z_OPT
        ZABCDCONTACTINDEX.Z_PK
        ZABCDEMAILADDRESS.Z21_OWNER
        ZABCDEMAILADDRESS.Z22_OWNER
        ZABCDEMAILADDRESS.ZADDRESSNORMALIZED
        ZABCDEMAILADDRESS.ZISPRIMARY
        ZABCDEMAILADDRESS.ZORDERINGINDEX
        ZABCDEMAILADDRESS.ZOWNER
        ZABCDEMAILADDRESS.ZUNIQUEID
        ZABCDEMAILADDRESS.Z_ENT
        ZABCDEMAILADDRESS.Z_OPT
        ZABCDEMAILADDRESS.Z_PK
        ZABCDNOTE.Z22_CONTACT
        ZABCDPHONENUMBER.Z21_OWNER
        ZABCDPHONENUMBER.Z22_OWNER
        ZABCDPHONENUMBER.ZIOSLEGACYIDENTIFIER
        ZABCDPHONENUMBER.ZISPRIMARY
        ZABCDPHONENUMBER.ZLASTFOURDIGITS
        ZABCDPHONENUMBER.ZORDERINGINDEX
        ZABCDPHONENUMBER.ZOWNER
        ZABCDPHONENUMBER.ZUNIQUEID
        ZABCDPHONENUMBER.Z_ENT
        ZABCDPHONENUMBER.Z_OPT
        ZABCDPHONENUMBER.Z_PK
        ZABCDPOSTALADDRESS.Z21_OWNER
        ZABCDPOSTALADDRESS.ZISPRIMARY
        ZABCDPOSTALADDRESS.ZOWNER
        ZABCDPOSTALADDRESS.Z22_OWNER
        ZABCDPOSTALADDRESS.ZUNIQUEID
        ZABCDPOSTALADDRESS.Z_ENT
        ZABCDPOSTALADDRESS.Z_OPT
        ZABCDPOSTALADDRESS.Z_PK
        ZABCDRECORD.ZCONTACTINDEX
        ZABCDRECORD.ZCONTAINER1
        ZABCDRECORD.ZCONTAINERWHERECONTACTISME
        ZABCDRECORD.ZCREATIONDATE
        ZABCDRECORD.ZCREATIONDATEYEAR
        ZABCDRECORD.ZCREATIONDATEYEARLESS
        ZABCDRECORD.ZDISPLAYFLAGS
        ZABCDRECORD.ZEXTERNALCOLLECTIONPATH
        ZABCDRECORD.ZEXTERNALFILENAME
        ZABCDRECORD.ZEXTERNALHASH
        ZABCDRECORD.ZEXTERNALMODIFICATIONTAG
        ZABCDRECORD.ZEXTERNALUUID
        ZABCDRECORD.ZIOSLEGACYIDENTIFIER
        ZABCDRECORD.ZLINKID
        ZABCDRECORD.ZMODIFICATIONDATE
        ZABCDRECORD.ZMODIFICATIONDATEYEAR
        ZABCDRECORD.ZMODIFICATIONDATEYEARLESS
        ZABCDRECORD.ZNOTE
        ZABCDRECORD.ZPREFERREDFORLINKNAME
        ZABCDRECORD.ZPREFERREDFORLINKPHOTO
        ZABCDRECORD.ZSORTINGFIRSTNAME
        ZABCDRECORD.ZSORTINGLASTNAME
        ZABCDRECORD.ZSOURCEWHERECONTACTISME
        ZABCDRECORD.ZSYNCSTATUS
        ZABCDRECORD.ZTHUMBNAILIMAGEDATA
        ZABCDRECORD.Z_ENT
        ZABCDRECORD.Z_OPT
        ZABCDRECORD.Z_PK
        ZABCDURLADDRESS.Z21_OWNER
        ZABCDURLADDRESS.ZISPRIMARY
        ZABCDURLADDRESS.ZOWNER
        ZABCDURLADDRESS.ZUNIQUEID
        ZABCDURLADDRESS.Z_ENT
        ZABCDURLADDRESS.Z_OPT
        ZABCDURLADDRESS.Z_PK
        ZABCDURLADDRESS.Z22_OWNER
        ''') if s.strip()]

    new_key_names = { \
            'ZABCDRECORD.ZFIRSTNAME'         : 'first'         ,
            'ZABCDRECORD.ZLASTNAME'          : 'last'          ,
            'ZABCDRECORD.ZORGANIZATION'      : 'organization'  ,
            'ZABCDEMAILADDRESS.ZADDRESS'     : 'email'         ,
            'ZABCDEMAILADDRESS.ZLABEL'       : 'email type'    ,
            'ZABCDPHONENUMBER.ZFULLNUMBER'   : 'phone'         ,
            'ZABCDPHONENUMBER.ZLABEL'        : 'phone type'    ,
            'ZABCDURLADDRESS.ZURL'           : 'url'           ,
            'ZABCDURLADDRESS.ZLABEL'         : 'url type'      ,
            'ZABCDPOSTALADDRESS.ZSTREET'     : 'street'        ,
            'ZABCDPOSTALADDRESS.ZCITY'       : 'city'          ,
            'ZABCDPOSTALADDRESS.ZSTATE'      : 'state'         ,
            'ZABCDPOSTALADDRESS.ZZIPCODE'    : 'zip'           ,
            'ZABCDPOSTALADDRESS.ZCOUNTRYNAME': 'country'       ,
            'ZABCDPOSTALADDRESS.ZCOUNTRYCODE': 'country code'  ,
            'ZABCDPOSTALADDRESS.ZLABEL'      : 'address type'  ,
            'ZABCDRECORD.ZUNIQUEID'          : 'uid'           
        }


    # For each Contact dict, delete worthless keys and rename other keys.
    #
    cs = [ { new_key_names.get(k,k) : v for k,v in d.items() if k not in keys_to_delete } for d in cs]


    # Remove :ABPerson suffix on UIDs.
    #
    for d in cs:
        d['uid'] = d['uid'].replace(':ABPerson','')

    # For phone, email, urls, convert    
    # 
    #   'phone': '+123-123-1234',
    #   'phone type': '_$!<Mobile>!$_',
    #
    # into like how 'ps' does it:
    #
    # 'phone': [('Mobile', '123-123-1234'), ...]
    #
    for d in cs:
        for k in ['phone','url','email']:
            if k in d:
                ktype = k + ' type'
                if ktype in d: # normal case
                    lab = d[ktype].replace('_$!<','').replace('>!$_','')
                else:
                    lab = '' # hack around the rare case where there's no 'phone type'
                val = d[k]
                d[k] = [(lab,val)]
                if ktype in d:
                    d.pop(ktype)

    # For address, gather relevant fields into a dict, ie, convert
    #
    # {'address type': '_$!<Work>!$_',
    #   'city': 'Cupertino',
    #   'country': 'United States',
    #   'country code': 'us',
    #   'organization': 'Apple Inc.',
    #   'phone': '1-800-MY-APPLE',
    #   'phone type': '_$!<Main>!$_',
    #   'state': 'CA',
    #   'street': '1 Infinite Loop',
    #   'uid': 'C13384AC-D081-4190-B5CB-DAEEE889A64D:ABPerson',
    #   'url': 'http://www.apple.com',
    #   'url type': '_$!<HomePage>!$_',
    #   'zip': '95014'},
    #
    # into
    #
    # {'address': [('Work',
    #                {'city': 'Cupertino',
    #                 'country': 'United States',
    #                 'country code': 'us',
    #                 'state': 'CA',
    #                 'street': '1 Infinite Loop',
    #                 'zip': '95014'})],
    #   'organization': 'Apple Inc.',
    #   'phone': [('Main', '1-800-MY-APPLE')],
    #   'uid': 'C13384AC-D081-4190-B5CB-DAEEE889A64D',
    #   'url': [('HomePage', 'http://www.apple.com')]}    
    #
    #
    ktype = 'address type'
    addr_keys = ['street','city','state','zip','country','country code']
    for d in cs:
        if ktype in d:
            t = d[ktype].replace('_$!<','').replace('>!$_','')
            a = {k: d[k] for k in addr_keys if k in d}
            d['address'] = [(t,a)]
            [d.pop(k) for k in addr_keys+[ktype]]
        else:
            if any(k in d for k in addr_keys):
                raise ValueError(f"Found some address-related fields, but no 'address type'!: {d}")


    # Merge contacts who have the same UID.
    #
    if not duplicate_freeQ(cs, lambda c: c['uid']):
        print(f"INFO: UID isn't unique, prob bc some contact has multiple types of phone / email / url / address. Merging...")
        print(f'Merging {len(cs)} contacts by UID...')
        cs = list(map(merge_dicts, gather(cs,lambda c: c['uid'])))
        print(f'Done; now have {len(cs)} contacts.')

    assert duplicate_freeQ(cs, lambda c: c['uid'])
    print(f'{len(cs)} UIDs are unique, good.')

    print(f'DONE: Cleaning {len(cs)} contacts.')
    return cs


def verify_people_are_subset_of_contacts(ps,cs):
    print(f"START: verify each .abcdp 'person' data ({len(ps)}) is a sub-dict of 1 db-based contact ({len(cs)}).")
    # (This ensures each .abcdp file is accounted for in the db-based contacts.)
    ps_to_remove = []
    for p in ps:
        ms = [c for c in cs if c['uid'] == p['uid']]
        assert len(ms)==1, "Each peep should share a uid with exactly 1 contact."
        m = ms[0]
        assert dict_subsetQ(p,m), "The peep's info (k/v pairs) should be a sub-dict of its matching contact."
        ps_to_remove.append(m)
    [ps.remove(p) for p in ps_to_remove]
    assert len(ps)==0, f"Every peep should match to exactly 1 contact, but instead, we have len(ps)={len(ps)}."
    print(f"Done.")

def merge_images_into_contacts(ims,cs):
    # for some reason, there are a lot of images that don't map to a contact.
    # there are also a lot of duplicate images.
    print(f"START: merge {len(ims)} ims into {len(cs)} contacts.")

    for c in cs:
        imss = [i for i in ims if i['base name'] == c['uid']]
        if imss:
            c['ims'] = imss
            if len(imss)>1:
                print(f"Warning: contact \n{pformat(c,indent=4)}\n has {len(imss)} duplicate images: \n{pformat(imss,indent=4)}\n")

    orphaned_ims = [i for i in ims if not any(c for c in cs if c['uid']==i['base name'])]

    print(f"DONE: merge {len(ims)} ims into {len(cs)} contacts.")
    print(f"{len([c for c in cs if 'ims' not in c])}/{len(cs)} contacts have no image (expect most cs to have no ims).")
    print(f"{len([c for c in cs if 'ims' in c and len(c['ims'])>1])}/{len(cs)} contacts have >1 image (dup ims are weird, but happen).")
    print(f"{len(orphaned_ims)}/{len(ims)} ims are orphaned (common to have orphaned ims bc many duplicates).")
    if len(orphaned_ims)>0:
        print("Example:")
        pp(orphaned_ims[0])

    return orphaned_ims, cs

def actually_copy_and_rename_image_files(cs, outdir):
    print(f"START: actually_copy_and_rename_image_files of {len(cs)} contacts' images into outdir={outdir}")
    print(f"Info: # contacts with 'ims': {len([c for c in cs if 'ims' in c and len(c['ims'])>0])}")
    for c in cs:
        if 'ims' in c:
            for i in c['ims']:
                b = i['base name']
                ext = i['image type']
                assert b
                assert ext

                fbase = '_'.join(
                        re.sub(r'[^a-zA-Z0-9_-]','',c[k].replace(' ','-')) 
                        for k in ['first','last','organization'] 
                        if k in c and re.sub(r'[^a-zA-Z0-9_]','',c[k])
                        )

                src = i['path']
                dst = outdir / (fbase + f"__{b}.{ext}")

                # if dst.is_file():
                #     print(f"Warning: overwriting {dst}")

                n = 1
                while dst.is_file():
                    n += 1
                    dst = outdir / (fbase + f"__{b}__{n}.{ext}")

                i['dst'] = dst

                # copy image files into new dir
                shutil.copy2(src,dst)
    print('Done.')

def actually_copy_and_rename_ORPHANED_image_files(ims, outdir):
    print(f"START: actually_copy_and_rename_ORPHANED_image_files of {len(ims)} contacts' images into outdir={outdir}")
    for i in ims:
        b = i['base name']
        ext = i['image type']
        assert b
        assert ext
        shutil.copy2(
            i['path'],
            outdir / f"{b}.{ext}"
            )
    print('Done.')


#################################################################################################



if __name__=='__main__':
    main()
