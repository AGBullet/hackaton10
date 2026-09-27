import hashlib, re, json, sqlite3
from pathlib import Path
from .config import ROOT, inside_root
from .db import query,one,execute,audit,connection,params

SUPPORTED = {'.pdf','.docx','.xml','.xlsx','.png','.jpg','.jpeg','.tif','.tiff'}
DENIED_PARTS = ('evidence_pages','разметк','выявления','эталон','gold','тз_','тз и','00_тз','02_формат','annotation','обзор','методик','readme')

def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        while b:=f.read(4*1024*1024): h.update(b)
    return h.hexdigest()

def uid(*parts):
    return hashlib.sha256('|'.join(map(str,parts)).encode()).hexdigest()[:24]

def infer_metadata(path):
    s=str(path).lower().replace('ё','е')
    # A combined folder is not evidence that every file is исполнительная документация.
    s=re.sub(r'рабочая\s+и\s+исполнительная\s+документация','смешанный комплект',s)
    stage='UNKNOWN'
    if re.search(r'исполнительн|(?:^|[\\/ _])ид(?:[\\/ _]|$)|аоср|аоок',s):stage='ID'
    elif re.search(r'рабоч(?:ая|ие|ей)|стадия р|(?:^|[\\/ _])рд(?:[\\/ _]|$)',s):stage='RD'
    elif re.search(r'проектн|стадия п|(?:^|[\\/ _])пд(?:[\\/ _]|$)',s):stage='PD'
    name=Path(path).stem
    disc=re.search(r'(?:^|[-_ .])(АР|КР|КЖ|КМ|ОВ|ВК|ЭОМ|ПЗУ|ПЗ|ПБ|ТХ|СС|ПОС|ООС|ОДИ)(?:[-_ .\d]|$)',name,re.I)
    rev=re.search(r'(?:изм[. _-]*|ред[. _-]*|rev[. _-]*)(\d+[а-яa-z]?)',name,re.I)
    code=re.sub(r'[ _-]*(?:изм|ред|rev)[. _-]*\d+[а-яa-z]?.*','',name,flags=re.I)
    return dict(stage=stage,discipline=disc.group(1).upper() if disc else 'UNKNOWN',document_code=code,revision=rev.group(1) if rev else None,approval_status='UNKNOWN',approval_date=None,metadata={'origin':'path_inference','needs_verification':True})

def create_object(name,object_id=None,address=''):
    object_id=object_id or 'OBJ-'+uid(name)[:12]
    execute('INSERT INTO objects(id,name,address) VALUES(%s,%s,%s) ON CONFLICT(id) DO NOTHING',(object_id,name,address))
    return object_id

def register(path,object_id,metadata=None,known_hash=None,allow_unsupported=False):
    path=inside_root(path)
    if path.suffix.lower() not in SUPPORTED and not allow_unsupported:
        raise ValueError('Неподдерживаемый формат')
    rel=str(path.relative_to(ROOT))
    existing=one('SELECT * FROM documents WHERE path=%s',(rel,))
    if existing:
        return existing
    obj=one('SELECT * FROM objects WHERE id=%s',(object_id,))
    if not obj: raise ValueError('Объект не найден')
    if obj['status']=='FINALIZED':raise ValueError('Протокол финализирован; создайте новую проверку')
    meta=infer_metadata(path)
    if metadata:meta.update({k:v for k,v in metadata.items() if k in {'stage','discipline','document_code','revision','approval_status','approval_date','predecessor_id','successor_id','signature_status','metadata'}})
    h=known_hash or sha256(path)
    file_id=uid(object_id,rel,h)
    execute('''INSERT INTO documents(id,object_id,name,path,sha256,size_bytes,format,stage,discipline,document_code,revision,approval_status,approval_date,predecessor_id,successor_id,signature_status,metadata)
    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(path) DO NOTHING''',
    (file_id,object_id,path.name,rel,h,path.stat().st_size,path.suffix.lower(),meta['stage'],meta['discipline'],meta['document_code'],meta['revision'],meta['approval_status'],meta['approval_date'],meta.get('predecessor_id'),meta.get('successor_id'),meta.get('signature_status','UNKNOWN'),meta['metadata']))
    if path.suffix.lower() not in SUPPORTED or path.name.startswith('~$'):execute("UPDATE documents SET parse_status='UNSUPPORTED',quality=%s WHERE id=%s",({'reason':'Служебный файл блокировки Office; не содержит документа' if path.name.startswith('~$') else 'Файл зарегистрирован; автоматическое извлечение формата не реализовано'},file_id))
    return one('SELECT * FROM documents WHERE path=%s',(rel,))

def raw_roots():
    journal=ROOT/'_extracted'/'extraction.sqlite'
    if not journal.exists():return []
    with sqlite3.connect(journal,timeout=30) as c:
        rows=c.execute('select path,destination,status from archives').fetchall()
    roots=[]
    allowed=set()
    for path,destination,status in rows:
        if status not in ('done','running'):continue
        p=Path(path)
        if p.parent==Path('.') and re.match(r'(?:01_|1[0-8]_)',p.name):
            roots.append((ROOT/destination,p.stem,Path()));allowed.add(str(ROOT/destination))
    # Only descend from raw-document archives. Annotation bundles are never application roots.
    changed=True
    while changed:
        changed=False
        for path,destination,status in rows:
            full=ROOT/path
            dest=ROOT/destination
            if status=='done' and str(dest) not in allowed and any(full.is_relative_to(Path(a)) for a in allowed):
                parent_base,parent_name,parent_prefix=next((base,name,prefix) for base,name,prefix in roots if full.is_relative_to(base))
                prefix=parent_prefix/full.relative_to(parent_base).parent
                roots.append((dest,parent_name,prefix));allowed.add(str(dest));changed=True
    return roots

def ingest_corpus(limit=0,callback=None):
    journal=ROOT/'_extracted/extraction.sqlite'
    hashes={}
    if journal.exists():
        with sqlite3.connect(journal,timeout=30) as c:
            hashes=dict(c.execute('select path,sha256 from members'))
    count=0
    existing_paths={r['path'] for r in query('SELECT path FROM documents')}
    object_cache={}
    pending=[]
    def flush():
        if not pending:return
        with connection() as db:
            db.cursor().executemany('''INSERT INTO documents(id,object_id,name,path,sha256,size_bytes,format,stage,discipline,document_code,revision,approval_status,approval_date,metadata,parse_status,quality)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(path) DO NOTHING''',pending)
        pending.clear()
        if callback:callback(count)
    for base,archive,prefix in raw_roots():
        for path in base.rglob('*'):
            if not path.is_file() or path.suffix.lower() in ('.zip','.7z','.tar','.rar','.part'):continue
            local=prefix/path.relative_to(base)
            if any(x in str(local).lower() for x in DENIED_PARTS):continue
            if archive.startswith('01_'):
                parts=local.parts
                if '01_ДОКУМЕНТАЦИЯ' not in parts:continue
                name=parts[parts.index('01_ДОКУМЕНТАЦИЯ')+1]
            else:name=archive[3:].replace('_',' ')
            rel=str(path.relative_to(ROOT))
            if rel in existing_paths:continue
            if name not in object_cache:
                candidate=create_object(name)
                object_cache[name]=candidate if one('SELECT status FROM objects WHERE id=%s',(candidate,))['status']!='FINALIZED' else None
            obj=object_cache[name]
            if obj is None:continue
            meta=infer_metadata(local)
            meta['metadata']['original_relative_path']=str(local)
            meta['metadata']['source_archive']=archive
            digest=hashes.get(rel) or sha256(path)
            unsupported=path.suffix.lower() not in SUPPORTED or path.name.startswith('~$')
            pending.append(params((uid(obj,rel,digest),obj,path.name,rel,digest,path.stat().st_size,path.suffix.lower(),meta['stage'],meta['discipline'],meta['document_code'],meta['revision'],meta['approval_status'],meta['approval_date'],meta['metadata'],'UNSUPPORTED' if unsupported else 'PENDING',{'reason':'Извлечение формата не реализовано'} if unsupported else {})))
            existing_paths.add(rel)
            count+=1
            if len(pending)>=100:flush()
            if limit and count>=limit:flush();return count
    flush()
    return count
