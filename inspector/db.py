import json,os,re,queue,threading
from contextlib import contextmanager
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from .config import PG, ROOT
SCHEMA=os.getenv('INSPECTOR_SCHEMA','inspector_local')
_pools={}
_pool_lock=threading.Lock()

def params(values):
    return tuple(Jsonb(v) if isinstance(v, (dict,list)) else v for v in values)

@contextmanager
def connection():
    if not re.fullmatch(r'[a-z_][a-z0-9_]*',SCHEMA):raise ValueError('Invalid database schema')
    # Reuse idle local connections, but give every scope its own transaction/lease.
    # Schema-keyed pools keep isolated real-data tests out of the working database.
    with _pool_lock:pool=_pools.setdefault(SCHEMA,queue.LifoQueue(maxsize=8))
    try:c=pool.get_nowait()
    except queue.Empty:c=None
    if c is None or c.closed or c.broken:
        c=psycopg.connect(**PG,row_factory=dict_row,options=f'-c search_path={SCHEMA},public')
    try:
        yield c
        c.commit()
    except BaseException:
        try:c.rollback()
        except psycopg.Error:c.close()
        raise
    finally:
        if not c.closed and not c.broken:
            try:pool.put_nowait(c)
            except queue.Full:c.close()

def query(sql, values=()):
    with connection() as c:
        return c.execute(sql, params(values)).fetchall()

def one(sql, values=()):
    rows = query(sql, values)
    return rows[0] if rows else None

def execute(sql, values=()):
    with connection() as c:
        cur = c.execute(sql, params(values))
        return cur.rowcount

def init():
    admin = dict(PG, dbname='postgres')
    with psycopg.connect(**admin, autocommit=True) as c:
        if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s', (PG['dbname'],)).fetchone():
            c.execute(psycopg.sql.SQL('CREATE DATABASE {}').format(psycopg.sql.Identifier(PG['dbname'])))
    with connection() as c:
        c.execute(psycopg.sql.SQL('CREATE SCHEMA IF NOT EXISTS {}').format(psycopg.sql.Identifier(SCHEMA)))
        c.execute((ROOT / 'inspector/schema.sql').read_text(encoding='utf-8'))

def audit(action, object_id=None, details=None, user='system', ip=None):
    execute('INSERT INTO audit(action,object_id,details,user_id,ip) VALUES(%s,%s,%s,%s,%s)', (action,object_id,details or {},user,ip))
