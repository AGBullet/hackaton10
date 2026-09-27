"""Safe, resumable extraction. Originals stay intact. SQLite is only an extraction journal."""
import argparse, hashlib, json, os, re, shutil, sqlite3, subprocess, tarfile, time, zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / '_extracted'
ARCHIVES = {'.zip', '.tar', '.7z', '.rar'}
RESERVE = 20 * 1024**3

def safe_target(base, name):
    name = name.replace('\\', '/')
    parts = PurePosixPath(name).parts
    if name.startswith('/') or re.match(r'^[A-Za-z]:',name) or any(p in ('..', '.') for p in parts):
        raise ValueError(f'Unsafe archive member: {name}')
    # Lossless escaping of names legal in ZIP but forbidden by Windows (e.g. <рот в рот>).
    parts = [re.sub(r'[<>:"|?*\x00-\x1f]', lambda m: f'%{ord(m[0]):02X}', part) for part in parts]
    parts = [re.sub(r'[ .]+$',lambda m:''.join(f'%{ord(c):02X}' for c in m[0]),part) for part in parts]
    p = base.joinpath(*parts).resolve()
    if not p.is_relative_to(base.resolve()):
        raise ValueError('Member escapes extraction root')
    return p

def space(size):
    if shutil.disk_usage(ROOT).free - size < RESERVE:
        raise RuntimeError('DISK_RESERVE: extraction paused, keep 20 GiB free')

def unpack(path, db, metadata_only=False):
    st = path.stat()
    key = hashlib.sha256(f'{path.relative_to(ROOT)}:{st.st_size}:{st.st_mtime_ns}'.encode()).hexdigest()[:16]
    dest = DEST / key
    row = db.execute('select status from archives where key=?', (key,)).fetchone()
    if row and row[0] == 'done':
        return dest
    dest.mkdir(parents=True, exist_ok=True)
    db.execute('insert or replace into archives values(?,?,?,?,?,?)', (key, str(path.relative_to(ROOT)), str(dest.relative_to(ROOT)), 'running', '', time.time()))
    db.commit()
    count = 0
    def write_member(name, size, source, display_name=None):
        nonlocal count
        target = safe_target(dest, display_name or name)
        if metadata_only and (Path(name).suffix.lower() not in {'.json', '.jsonl', '.ndjson', '.csv', '.docx', '.xlsx', '.md', '.txt'} or size > 15*1024**2):
            return
        stamp = db.execute('select size,path from members where archive=? and name=?', (key, name)).fetchone()
        if stamp and not target.exists():
            previous=(ROOT/stamp[1]).resolve()
            if previous.is_relative_to(DEST.resolve()) and previous.exists() and previous.stat().st_size==size:
                target.parent.mkdir(parents=True,exist_ok=True)
                previous.replace(target)
                db.execute('update members set path=? where archive=? and name=?',(str(target.relative_to(ROOT)),key,name));db.commit()
        if stamp and target.exists() and target.stat().st_size == size == stamp[0]:
            return
        space(size)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(target.name + '.part')
        h = hashlib.sha256()
        written = 0
        with source() as src, partial.open('wb') as out:
            while block := src.read(1024 * 1024):
                written += len(block)
                if written > size:
                    raise ValueError('Declared member size exceeded')
                h.update(block)
                out.write(block)
        if written != size:
            raise ValueError('Truncated archive member')
        partial.replace(target)
        db.execute('insert or replace into members values(?,?,?,?,?)', (key, name, size, h.hexdigest(), str(target.relative_to(ROOT))))
        db.commit()
        count += 1
        if count % 100 == 0:
            print(json.dumps({'archive': path.name, 'extracted_now': count, 'free_gib': round(shutil.disk_usage(ROOT).free/1024**3, 1)}, ensure_ascii=False), flush=True)
    try:
        if path.suffix.lower() == '.zip':
            with zipfile.ZipFile(path) as z:
                for i in z.infolist():
                    if i.is_dir():
                        continue
                    if (i.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError('Archive symlink rejected')
                    try:display=i.filename if i.flag_bits & 0x800 else i.filename.encode('cp437').decode('cp866')
                    except UnicodeEncodeError:display=i.filename # Unicode extra field already decoded by zipfile.
                    write_member(i.filename, i.file_size, lambda i=i: z.open(i), display)
        elif path.suffix.lower() == '.tar':
            with tarfile.open(path) as t:
                for i in t:
                    if i.issym() or i.islnk():
                        raise ValueError('Archive link rejected')
                    if i.isfile():
                        write_member(i.name, i.size, lambda i=i: t.extractfile(i))
        else:
            if metadata_only:
                return dest
            exe = r'C:\Program Files\7-Zip\7z.exe'
            listing = subprocess.run([exe, 'l', '-slt', '-sccUTF-8', str(path)], capture_output=True, encoding='utf-8', errors='replace', check=True).stdout
            section = listing.split('----------', 1)[-1]
            entries = [dict(line.split(' = ', 1) for line in b.splitlines() if ' = ' in line) for b in section.strip().split('\n\n')]
            for e in entries:
                if 'Path' in e:
                    safe_target(dest, e['Path'])
                if any(e.get(k) for k in ('Symbolic Link', 'Hard Link','Copy Link')) or e.get('Attributes', '').startswith('l'):
                    raise ValueError('Archive link rejected')
            space(sum(int(e.get('Size', 0)) for e in entries))
            subprocess.run([exe, 'x', '-y', '-aos', '-sccUTF-8', f'-o{dest}', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            # 7z checks CRC on extraction. On restart verify all members before marking complete.
            subprocess.run([exe, 't', '-y', str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, check=True)
            for e in entries:
                if e.get('Folder') != '+' and not e.get('Attributes','').startswith('D') and 'Path' in e and 'Size' in e:
                    p = safe_target(dest, e['Path'])
                    if not p.is_file() or p.stat().st_size != int(e['Size']):
                        raise ValueError('Missing or incomplete 7z output: '+e['Path'])
                    if not db.execute('select 1 from members where archive=? and name=?',(key,e['Path'])).fetchone():
                        with p.open('rb') as f:h=hashlib.file_digest(f,'sha256').hexdigest()
                        db.execute('insert or replace into members values(?,?,?,?,?)',(key,e['Path'],int(e['Size']),h,str(p.relative_to(ROOT))))
                        count+=1
                db.commit()
        state = 'metadata' if metadata_only else 'done'
        db.execute('update archives set status=?,updated=? where key=?', (state, time.time(), key))
        db.commit()
        print(json.dumps({'archive': str(path.relative_to(ROOT)), 'status': state, 'new_files': count}, ensure_ascii=False), flush=True)
    except Exception as e:
        db.execute('update archives set status=?,error=?,updated=? where key=?', ('paused' if 'DISK_RESERVE' in str(e) else 'error', str(e), time.time(), key))
        db.commit()
        print(json.dumps({'archive': str(path.relative_to(ROOT)), 'error': str(e)}, ensure_ascii=False), flush=True)
        if 'DISK_RESERVE' in str(e):
            raise
    return dest

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--metadata-only', action='store_true')
    parser.add_argument('--first', type=int, default=0)
    parser.add_argument('--repair-names', action='store_true')
    args = parser.parse_args()
    DEST.mkdir(exist_ok=True)
    db = sqlite3.connect(DEST / 'extraction.sqlite', timeout=60)
    db.execute('pragma journal_mode=WAL')
    db.execute('create table if not exists archives(key text primary key,path text,destination text,status text,error text,updated real)')
    db.execute('create table if not exists members(archive text,name text,size integer,sha256 text,path text,primary key(archive,name))')
    if args.repair_names:
        # Re-run completed ZIPs: journal size/hash lets us move decoded names without re-extracting.
        db.execute("update archives set status='metadata' where path like '%.zip'");db.commit()
    roots = sorted((p for p in ROOT.iterdir() if p.suffix.lower() in ARCHIVES), key=lambda p: (0 if p.name.startswith('02_') else 1, p.name))
    if args.first:
        roots = roots[:args.first]
    seen = set()
    queue = roots[:]
    while queue:
        p = queue.pop(0)
        if p in seen:
            continue
        seen.add(p)
        dest = unpack(p, db, args.metadata_only)
        if not args.metadata_only:
            queue.extend(f for f in dest.rglob('*') if f.suffix.lower() in ARCHIVES and f.is_file())
    db.close()

if __name__ == '__main__':
    main()
