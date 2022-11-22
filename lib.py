from subprocess import run
import sqlite3
from pprint import pprint as pp
from more_itertools import bucket
from collections import OrderedDict
from json import JSONEncoder
import datetime
from copy import deepcopy
from pathlib import Path, PosixPath
import json

##################################################
# Basic funcs
##################################################

def get_file_info(f):
    return run(['file','--brief',f],capture_output=True,check=True,text=True).stdout

def gather(lst, f):
    '''Force more_itertools's 'bucket' to have a more sensible API (like Mathematica's)... without all these iterators ;)'''
    dic = bucket(lst,key=f)
    return [list(dic[k]) for k in dic]

def diff_dicts(d1,d2):
    ks = set(list(d1.keys()) + list(d2.keys()))
    for i,k in enumerate(ks):
        if k in d1 and k not in d2:
            print(f'Key {i}/{len(ks)}: {k} in d1 and {k} not in d2')
        elif k not in d1 and k in d2:
            print(f'Key {i}/{len(ks)}: {k} not in d1 and {k} in d2')
        elif k not in d1 and k not in d2:
            print(f'Key {i}/{len(ks)}: WTF, {k} not in BOTH, should never get here!!')
        elif k in d1 and k in d2:
            if d1[k] == d2[k]:
                continue # normal case: both dicts have same val for a key.
            else:
                print(f'Key {i}/{len(ks)}: {k} DIFFERS:\n    LEFT: {d1[k]}\n    RIGHT: {d2[k]}')
        else:
            raise ValueError('Already covered all cases; should never get here.')


##################################################
# Funcs for DB stuff
##################################################

def table_names(db):
    '''Returns the names of the tables in the given sqlite db.'''
    return [r[0] for r in sqlite3.connect(db).cursor().execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]

def column_names(t,db):
    '''Returns the (unqualified) column names of table t in sqlite database db.'''
    return [ r[1] for r in sqlite3.connect(db).cursor().execute(f"PRAGMA table_info('{t}')").fetchall() ]

def num_rows(t,db):
    '''Gives the number of rows in table t of sqlite db.'''
    # >>> x = sqlite3.connect('AddressBook-v22.abcddb').cursor().execute(f"SELECT COUNT(1) FROM ZABCDRECORD")
    # >>> x.fetchall()
    # [(90,)]        
    assert t in table_names(db)
    return sqlite3.connect(db).cursor().execute(f"SELECT COUNT(1) FROM {t}").fetchall()[0][0]

def table_has_columnQ(t,c,db):
    return c in column_names(t,db)

def should_join_tableQ(t,db):
    '''Returns True if table t of database db is able to be left-joined to the main table of Contacts ("ZABCDRECORD")
    for the purposes of querying a single large unified table of contacts.

    Many tables have a ZOWNER or ZCONTACT column which links to a row of ZABCDRECORD via eg ZOWNER = ZABCDRECORD.Z_PK.

    Ignore blacklisted tables, like dumb tables like ZABCDCUSTOMPROPERTYVALUE, which contains the font & font size the contact should display as.
    If you don't, you'll have duplicate rows in your resulting left-join table, one dup for each font / font size / font color, ...
    '''
    if t == 'ZABCDRECORD': return False # This is the "main" table of records, that we will be joining other tables to.
    if t in ['ZABCDCUSTOMPROPERTYVALUE']: return False
    return any(table_has_columnQ(t,c,db) for c in ['ZOWNER','ZCONTACT'])

def join_condition(t,db):
    '''Returns a SQL snippet for left-joining the given table to the main ZABCDRECORD table,
    based on the presence of columns ZOWNER or ZCONTACT in the given table.
    '''
    predicate = ' OR '.join(f'({t}.{c} = ZABCDRECORD.Z_PK)' for c in ['ZOWNER','ZCONTACT'] if table_has_columnQ(t,c,db))
    return f'LEFT JOIN {t} ON ({predicate})'

def select_subclause(t,db):
    '''Returns a SQL snippet for selecting the given table's relevant columns, eg, 

    TAB.COL1 as 'TAB.COL1'
    TAB.COL2 as 'TAB.COL2'
    TAB.COL3 as 'TAB.COL3'
    ...

    Can't just SELECT *, because sqlite doesn't scope col names by table name,
    eg, if 2 tables have column 'foo', resulting table from joining will have
    2 columns both named 'foo' -- bad. So force uniqueness by specifying table_name.column_name .
    '''

    return '\n   , '.join( f"{t}.{c} as '{t}.{c}'" for c in column_names(t,db) )




#########################################
# Main db-to-dicts method.
#########################################

def parse_abcddb(db : Path):
    '''Return a list of Contacts as dicts from a Mac Address Book sqlite db like 'My Contacts.abbu/AddressBook-v22.abcddb'.
        NOTE: The UID column ('ZABCDRECORD.ZUNIQUEID') will be non-unique in the list of returned dicts
        if a contact had multiple types of phone / email / url / address values. There's probably some way
        to GROUP BY in the sql, but I'd rather resolve it in python.
    '''
    print(f'START: parse abcddb file {db} .')

    assert str(db).endswith('.abcddb')
    assert 'SQLite 3.x database' in get_file_info(db)

    #################################
    # Print DB Info
    #################################

    print(f'In db {db}, # tables = {len(table_names(db))}')

    t_infos = [{ 'name': t, 
                 'num cols': len(column_names(t,db)),
                 'num rows': num_rows(t,db),
                 'should join?': should_join_tableQ(t,db)
               }
                 for t in table_names(db)
              ]

    x = [t for t in t_infos if t['should join?']]
    print(f"{len(x)} tables should join:")
    for e in x:
        print(f"{e['name']} -- {e['num cols']} cols -- {e['num rows']} rows")

    print('\n\n------------------------------\n\n')

    x = [t for t in t_infos if not t['should join?']]
    print(f"{len(x)} tables NOT should join:")
    for e in x:
        print(f"{e['name']} -- {e['num cols']} cols -- {e['num rows']} rows")

    print('\n\n------------------------------\n\n')



    ##############################
    # Query.
    ##############################

    main_table = 'ZABCDRECORD'
    joined_tables = [t for t in table_names(db) if should_join_tableQ(t,db)]
    all_tables = [main_table] + joined_tables
    select_clause = '\n , '.join(select_subclause(t,db) for t in all_tables)
    join_clause = main_table + '\n' + '\n'.join(join_condition(t,db) for t in joined_tables)
    q = f'''SELECT
    {select_clause}
    FROM
    {join_clause}
    '''
    print('~~~~~~~ QUERY: ~~~~~~~~~')
    print(q)
    print('~~~~~~~ END QUERY ~~~~~~~~~')
    print('Querying...')
    x = sqlite3.connect(db).cursor().execute(q)
    print('Done w/ query.')
    rs = x.fetchall()
    cs = [r[0] for r in x.description]
    print(f'Fetched {len(rs)} rows, {len(cs)} cols.')
    print("Convert to dicts, remove 'null' data...")
    ds = [dict((k,v) for k,v in zip(cs,r) if v) for r in rs]  # "if v" to omit keys that are None, 0, '', [], ...
    print(f'Done parsing {db}, returning {len(ds)} Contact dicts.')
    if len(ds)>0:
        print('Example dict:')
        pp(ds[-1])
    return ds



def merge_dicts(dlist : list):
    '''Smoosh the given dicts into a single dict: Take the first dict,
    then add new key-value pairs in subsequent dicts.
    For colliding keys whose values are lists, append new values.
    Raise error for colliding keys whose values are NOT lists.
    '''
    if len(dlist)==1:
        return dlist[0]
    else:
        z = deepcopy(dlist[0])
        for y in dlist[1:]:
            for k,v in y.items():
                if k not in z:
                    z[k] = v
                else: # key collision
                    if z[k] == v:
                        continue # ok, already have it
                    else:
                        # vals differ. if vals are lists, concat them. else, complain.
                        if type(z[k]) == list and type(v) == list:
                            z[k].extend(v)
                        else:
                            raise ValueError(f"Uh oh: different vals and non-lists: key '{k}', z[k] = '{z[k]}'', y[k] = '{y[k]}'")
    return z


def duplicate_freeQ(lst, f):
    dup_groups = list(filter(lambda g: len(g)!=1, gather(lst,f)))
    if len(dup_groups)>0:
        print(f"WARNING: {sum(len(g) for g in dup_groups)}/{len(lst)} elems are duplicates; {len(dup_groups)} subsets:")
        pp(dup_groups)
        return False
    else:
        return True

def dict_subsetQ(x,y):
    # If dict y contains all of x's k/v pairs already.
    return all(k in y and v==y[k] for k,v in x.items())


# For exporting: json can't export datetime. extend json.dumps to convert a datetime into isoformat, eg:
#       x.isoformat()
#       => '2020-08-29T20:39:13.248940'
# src:
# https://pynative.com/python-serialize-datetime-into-json/
#
# Also export any Path or PosixPath into its str form.
#
class DateTimeEncoder(JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        elif isinstance(obj, (Path, PosixPath)):
            return str(obj)


def export(obj,f):
    '''Export list/dict object obj into json file f,
       serializing datetimes into isoformat '2020-08-29T20:39:13.248940'.
    '''
    print(f'START: Exporting {type(obj)} of len {len(obj)} to file {f}')

    open(f,'w').write(json.dumps(obj,
        indent=4,
        cls=DateTimeEncoder
        ))

    print(f'DONE: Exporting to file {f}')
