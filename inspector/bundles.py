"""Attach official TRAIN/TEST source packs. TRAIN public checks become review cards. TEST labels stay out of the database."""
import json, zipfile
from collections import defaultdict
import fitz
from .config import ROOT
from .db import query, one, execute, audit
from .registry import uid
from .extraction import parse_document, scalar
from .catalog import catalog

LOCAL_OBJECTS = {
    'OBJ-TYUMENSKAYA-5-GOLD-SEED': 'OBJ-fdc71cc813c8',
    'OBJ-NOVOSLOBODSKAYA': 'OBJ-a42c523b697c',
    'OBJ-RECHNIKOV-7-7': 'OBJ-c61f6a8db850',
}
PUBLIC_CODES = {'IOS4-079': 'M-079', 'IOS4-078': 'M-078', 'PZ-009': 'M-009', 'KR-055': 'M-055', 'KR-058': 'M-058'}
VERSION = 'bundle-train-v2'
EXPLAIN = {
    'MISSING_DESIGN_ELEMENT': 'В РД отсутствует элемент, предусмотренный ПД.',
    'CONFIGURATION_MISMATCH': 'Конфигурация элемента в РД отличается от ПД.',
    'EQUAL_AFTER_DECIMAL_NORMALIZATION': 'Значения совпадают после нормализации записи.',
    'EQUAL_ALL_AVAILABLE_STAGES': 'Значения совпадают во всех доступных стадиях.',
    'EQUAL_PD_RD': 'Значения ПД и РД совпадают.',
    'NON_TRIGGERING_DIFFERENCE_NO_DECREASE': 'Числа различаются, но снижение относительно ПД не подтверждено.',
}


def _jsonl(z, suffix):
    name = next(n for n in z.namelist() if n.endswith(suffix))
    return [json.loads(line) for line in z.read(name).decode('utf-8-sig').splitlines() if line.strip()]


def _docs_by_sha(shas):
    if not shas:
        return {}
    rows = query('SELECT * FROM documents WHERE sha256 IN (' + ','.join(['%s'] * len(shas)) + ')', tuple(shas))
    by = defaultdict(list)
    for row in rows:
        by[row['sha256']].append(row)
    return by


def _value(raw, unit=''):
    text = '' if raw is None else str(raw).strip()
    if not text:
        text = 'не указано'
    try:
        return scalar(text, unit or '')
    except ValueError:
        return {'raw': text, 'kind': 'text', 'unit': unit or '', 'normalized': text}


def _pick_doc(candidates, object_id, stage):
    same = [d for d in candidates if d['object_id'] == object_id]
    pool = same or candidates
    wanted = 'RD' if stage in ('RD', 'RD_ID_MIXED') else stage
    staged = [d for d in pool if d['stage'] == wanted]
    return (staged or pool)[0]


def _normalize_bbox(box, precision):
    """Keep precise zones; never store a full-page wash as a fragment."""
    if not box or len(box) != 4 or precision == 'PAGE_LEVEL_ONLY':
        return None, 'PAGE_LEVEL_ONLY'
    x0, y0, x1, y1 = [float(v) for v in box]
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None, 'PAGE_LEVEL_ONLY'
    area = (x1 - x0) * (y1 - y0)
    if area >= 0.25:
        return None, 'PAGE_LEVEL_ONLY'
    # Tiny number/label anchors: expand slightly so the inspector can see them.
    w, h = x1 - x0, y1 - y0
    if w < 0.035 or h < 0.035:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        w, h = max(w, 0.05), max(h, 0.05)
        x0, y0 = max(0.0, cx - w / 2), max(0.0, cy - h / 2)
        x1, y1 = min(1.0, cx + w / 2), min(1.0, cy + h / 2)
        return [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)], precision or 'TEXT_EXACT'
    return [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)], precision or 'TEXT_EXACT'


def _annotation_index(rows):
    by = {}
    for r in rows:
        if r.get('annotation_type') not in ('CONFIRMED_VIOLATION_EVIDENCE', 'SECOND_REVIEW_CHECK'):
            continue
        if not r.get('check_id') or not r.get('file_id'):
            continue
        by[(r['check_id'], r['file_id'])] = r
    return by


def _explain(check):
    cmp = check.get('comparison_result') or ''
    base = EXPLAIN.get(cmp, 'См. указанные листы ПД и РД/ИД.')
    note = (check.get('review_note') or '').strip()
    return ' '.join(x for x in (base, note) if x)


def import_train(parse_pages=True):
    bundle = ROOT / 'РАЗМЕЧЕННЫЙ_TRAIN_PUBLIC_203.zip'
    with zipfile.ZipFile(bundle) as z:
        index = [r for r in _jsonl(z, 'data/files_index.jsonl') if r.get('split') == 'TRAIN_PUBLIC']
        checks = [r for r in _jsonl(z, 'data/public_gold_checks.jsonl') if r.get('split') == 'TRAIN_PUBLIC']
        annotations = _annotation_index([r for r in _jsonl(z, 'data/annotations.jsonl') if r.get('split') == 'TRAIN_PUBLIC'])
    by_file = {r['file_id']: r for r in index}
    docs = _docs_by_sha([r['source_sha256'] for r in index])
    parameters = {p['code']: p for p in catalog()}
    needed = defaultdict(set)
    created = []
    skipped = []
    for check in checks:
        local_id = LOCAL_OBJECTS[check['object_id']]
        obj = one('SELECT * FROM objects WHERE id=%s', (local_id,))
        if not obj or obj['status'] == 'FINALIZED':
            skipped.append({'check_id': check['check_id'], 'reason': 'object_finalized_or_missing'})
            continue
        code = PUBLIC_CODES.get(check.get('parameter_code'))
        if check.get('parameter_code', '').startswith('FREE-'):
            code = None
        if code and code not in parameters:
            skipped.append({'check_id': check['check_id'], 'reason': 'unknown_parameter'})
            continue
        evidence = []
        ok = True
        for item in check.get('evidence', []):
            meta = by_file[item['file_id']]
            doc = _pick_doc(docs[meta['source_sha256']], local_id, item['stage'])
            if doc['object_id'] != local_id:
                ok = False
                skipped.append({'check_id': check['check_id'], 'reason': 'source_on_other_object', 'file_id': item['file_id']})
                break
            stage = doc['stage']
            if stage not in ('PD', 'RD', 'ID'):
                ok = False
                skipped.append({'check_id': check['check_id'], 'reason': 'stage_unknown', 'file_id': item['file_id']})
                break
            role = 'expected' if stage == 'PD' else 'actual'
            raw = check['pd_value'] if role == 'expected' else (check.get('rd_value') if stage == 'RD' else check.get('id_value') or check.get('rd_value'))
            quote = str(raw).strip() if raw is not None else 'значение на указанном листе'
            unit = (parameters[code]['unit'] if code else '') or ''
            value = _value(raw if raw is not None else quote, unit)
            if value['raw'].casefold().replace(' ', '') not in quote.casefold().replace(' ', ''):
                quote = f'{quote} ({value["raw"]})'
            ann = annotations.get((check['check_id'], item['file_id']))
            page = (ann or {}).get('page_number') or item['pdf_page_number']
            bbox, localization = _normalize_bbox((ann or {}).get('bbox_normalized'), (ann or {}).get('location_precision') or item.get('localization'))
            # Storage always keeps a bbox for schema validators; page-level uses a non-drawn marker flag.
            store_bbox = bbox if bbox is not None else [0.0, 0.0, 0.001, 0.001]
            evidence.append({
                'file_id': doc['id'], 'sha256': doc['sha256'], 'stage': stage, 'page': page,
                'bbox': store_bbox, 'quote': quote, 'value': value, 'role': role,
                'document_code': doc['document_code'], 'revision': doc['revision'],
                'approval_status': doc['approval_status'], 'entity': check.get('location') or 'OBJECT',
                'coordinate_space': 'visible_rotated_page_normalized',
                'localization': localization,
                'fragment_found': localization != 'PAGE_LEVEL_ONLY',
                'bundle_file_id': item['file_id'],
                'demo': True,
            })
            needed[doc['id']].add(page)
        if not ok:
            continue
        if not any(e['role'] == 'expected' for e in evidence) or not any(e['role'] == 'actual' for e in evidence):
            skipped.append({'check_id': check['check_id'], 'reason': 'missing_pd_or_rd'})
            continue
        for ev in evidence:
            doc = one('SELECT * FROM documents WHERE id=%s', (ev['file_id'],))
            if not one('SELECT id FROM revision_choices WHERE object_id=%s AND file_id=%s', (local_id, doc['id'])):
                execute('INSERT INTO revision_choices(object_id,stage,document_code,file_id,user_id,reason) VALUES(%s,%s,%s,%s,%s,%s)',
                        (local_id, doc['stage'], doc['document_code'], doc['id'], 'system',
                         'Выбранные рабочие источники проверки ' + check['check_id']))
        expected = next(e['value'] for e in evidence if e['role'] == 'expected')
        actual = next(e['value'] for e in evidence if e['role'] == 'actual')
        if check['violation_label'] == 'VIOLATION_PRESENT':
            status = 'CANDIDATE'
        else:
            status = 'MATCH_PENDING_REVIEW'
        entity = check.get('location') or 'OBJECT'
        param = parameters.get(code) if code else None
        fid = uid('bundle-train', check['check_id'], local_id)
        pages_only = not all(e.get('fragment_found') for e in evidence)
        rationale = _explain(check)
        execute('''INSERT INTO findings(id,object_id,parameter_code,rule_code,entity,machine_status,expected_value,actual_value,delta,evidence,rationale,priority,fingerprint,model_version)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET active=true,machine_status=excluded.machine_status,evidence=excluded.evidence,rationale=excluded.rationale,expected_value=excluded.expected_value,actual_value=excluded.actual_value,model_version=excluded.model_version,delta=excluded.delta''',
                  (fid, local_id, code, check.get('parameter_code') or 'FREE_SEARCH', entity, status, expected, actual,
                   {'bundle_check': check['check_id'], 'comparison_result': check.get('comparison_result'), 'demo': True, 'origin': 'TRAIN_PUBLIC_DEMONSTRATION'},
                   evidence, rationale, (param or {}).get('priority') or 'HIGH', fid, VERSION))
        if code:
            execute('''INSERT INTO checks(object_id,parameter_code,implementation_status,completeness_status,finding_status,details)
                VALUES(%s,%s,%s,%s,%s,%s)
                ON CONFLICT(object_id,parameter_code) DO UPDATE SET finding_status=excluded.finding_status,completeness_status=excluded.completeness_status,details=excluded.details,updated_at=now()''',
                      (local_id, code, 'PARTIAL_LITERAL_ONLY', 'COMPLETE' if status == 'CANDIDATE' else status, status,
                       {'bundle_check': check['check_id'], 'source': 'TRAIN_PUBLIC_DEMONSTRATION', 'gold': False, 'demo': True}))
        created.append({'check_id': check['check_id'], 'finding_id': fid, 'object_id': local_id, 'status': status, 'parameter_code': code, 'pages_only': pages_only})
    parsed = []
    if parse_pages:
        for file_id, pages in needed.items():
            parsed.append({'file_id': file_id, **parse_document(file_id, allow_ocr=True, page_numbers=sorted(pages))})
    else:
        for file_id in needed:
            doc = one('SELECT * FROM documents WHERE id=%s', (file_id,))
            with fitz.open(ROOT / doc['path']) as pdf:
                execute('UPDATE documents SET page_count=%s WHERE id=%s', (len(pdf), file_id))
    execute("UPDATE objects SET status='READY', name=%s, address=%s WHERE id=%s AND status!='FINALIZED'",
            ('Проверка изменений на чертежах', 'Тюменская, 5', 'OBJ-fdc71cc813c8'))
    audit('bundle_train_import', 'OBJ-fdc71cc813c8', {'created': len(created), 'skipped': skipped, 'version': VERSION})
    return {'created': created, 'skipped': skipped, 'parsed': parsed, 'index_files': len(index), 'checks': len(checks)}


def import_test_sources():
    """Register coverage of TEST source bytes only. Label JSONL is not read."""
    bundle = ROOT / 'РАЗМЕЧЕННЫЙ_TEST__213.zip'
    with zipfile.ZipFile(bundle) as z:
        index = _jsonl(z, 'data/files_index.jsonl')
    sources = [{k: r.get(k) for k in ('file_id', 'object_id', 'stage', 'source_sha256', 'source_relative_path', 'extension', 'split')} for r in index]
    docs = _docs_by_sha([r['source_sha256'] for r in sources if r.get('source_sha256')])
    rows = []
    missing = []
    for item in sources:
        found = docs.get(item['source_sha256']) or []
        if not found:
            missing.append({**item, 'reason': 'source_not_registered'})
            continue
        doc = found[0]
        rows.append({'file_id': item['file_id'], 'local_document_id': doc['id'], 'object_id': doc['object_id'],
                     'stage': doc['stage'], 'parse_status': doc['parse_status'], 'name': doc['name'],
                     'extension': item.get('extension')})
        meta = dict(doc.get('metadata') or {})
        if meta.get('bundle_test_file_id') != item['file_id']:
            meta['bundle_test_file_id'] = item['file_id']
            meta['bundle_test_split'] = 'ARCHIVE_TEST_SOURCES'
            execute('UPDATE documents SET metadata=%s WHERE id=%s', (meta, doc['id']))
    report = {
        'bundle': 'РАЗМЕЧЕННЫЙ_TEST__213.zip',
        'labels_imported': False,
        'gold_used_for_thresholds': False,
        'note': 'Архивный пакет с именем test_hidden. По Q&A №7/№9 это не финальная скрытая выборка. Метки не импортированы.',
        'index_files': len(sources),
        'matched_sources': len(rows),
        'missing_sources': missing,
        'matched': rows,
    }
    out = ROOT / 'reports' / 'bundles'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'test_source_coverage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    audit('bundle_test_sources', LOCAL_OBJECTS['OBJ-RECHNIKOV-7-7'], {'matched': len(rows), 'missing': len(missing), 'labels_imported': False})
    return {'matched': len(rows), 'missing': len(missing), 'labels_imported': False, 'report': str((out / 'test_source_coverage.json').relative_to(ROOT))}


def import_official_bundles(parse_pages=True):
    train = import_train(parse_pages=parse_pages)
    test = import_test_sources()
    summary = {'train': {'created': len(train['created']), 'skipped': train['skipped'], 'parsed': train['parsed'], 'cases': train['created']}, 'test': test}
    out = ROOT / 'reports' / 'bundles'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'import.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return summary
