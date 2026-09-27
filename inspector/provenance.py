"""Allowlist of source bytes only. Labels and hidden bundles are never read here."""
import json,zipfile
from functools import lru_cache
from .config import ROOT

@lru_cache(maxsize=1)
def public_source_hashes():
    bundle=ROOT/'РАЗМЕЧЕННЫЙ_TRAIN_PUBLIC_203.zip'
    if not bundle.exists():return frozenset()
    with zipfile.ZipFile(bundle) as z:
        name=next(n for n in z.namelist() if n.endswith('/data/files_index.jsonl'))
        rows=(json.loads(line) for line in z.read(name).decode('utf-8-sig').splitlines() if line.strip())
        return frozenset(r['source_sha256'] for r in rows if r.get('split')=='TRAIN_PUBLIC')
