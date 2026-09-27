"""Document counters share the same current evidence and decisions as inspector cards."""
from .comparison import findings
from .db import one,query

CATEGORIES={
    'possible': {'CANDIDATE','SUSPICION'},
    'confirmed': {'CONFIRMED_VIOLATION'},
    'clarification': {'CLARIFICATION_REQUIRED'},
}

def current_findings(object_id):
    obj=one('SELECT status FROM objects WHERE id=%s',(object_id,))
    if obj and obj['status']=='FINALIZED':
        saved=one('SELECT snapshot FROM protocols WHERE object_id=%s ORDER BY version DESC LIMIT 1',(object_id,))
        if saved:
            sections=saved['snapshot']['sections']
            return [f for key in ('candidates','confirmed_violations','negative_verified','suspicions','requires_clarification','machine_matches_pending_review','machine_nontriggers_pending_review') for f in sections.get(key,[])]
    return findings(object_id)

def document_findings(object_id,file_id,category=None):
    if category and category not in CATEGORIES:raise ValueError('Неизвестный фильтр находок')
    selected={}
    for finding in current_findings(object_id):
        if category and finding['status'] not in CATEGORIES[category]:continue
        evidence=[e for e in finding.get('current_evidence',finding['evidence']) if e.get('file_id')==file_id]
        if evidence:selected[finding['id']]={**finding,'document_evidence':evidence}
    return list(selected.values())

def documents_with_findings(object_id,stage=None,category=None,sort='name',offset=0,limit=500):
    from .coverage import document_reason
    if category and category not in (*CATEGORIES,'any','unreviewed'):raise ValueError('Неизвестный фильтр документов')
    if sort not in ('name','possible','confirmed','clarification','findings'):raise ValueError('Неизвестная сортировка')
    rows=query('SELECT * FROM documents WHERE object_id=%s'+(' AND stage=%s' if stage else ''),(object_id,stage) if stage else (object_id,))
    related={}
    for f in current_findings(object_id):
        for file_id in {e.get('file_id') for e in f.get('current_evidence',f['evidence'])}:
            related.setdefault(file_id,{})[f['id']]=f
    for d in rows:
        d['processing_reason']=document_reason(d)
        fs=list(related.get(d['id'],{}).values())
        d['finding_counts']={cat:sum(f['status'] in states for f in fs) for cat,states in CATEGORIES.items()}
        d['finding_counts']['total']=sum(d['finding_counts'].values())
        d['finding_ids']=[f['id'] for f in fs]
        d['review_state']='PARTIALLY_REVIEWED' if any(f['status'] in ('CONFIRMED_VIOLATION','NEGATIVE_VERIFIED') for f in fs) else 'NOT_REVIEWED'
        d['review_label']='Есть решения по отдельным находкам' if d['review_state']=='PARTIALLY_REVIEWED' else 'Проверка инспектором не завершена'
    if category:
        rows=[d for d in rows if (d['review_state']=='NOT_REVIEWED' if category=='unreviewed' else d['finding_counts']['total' if category=='any' else category]>0)]
    rows.sort(key=lambda d:((-(d['finding_counts']['total' if sort=='findings' else sort])) if sort!='name' else 0,d['name'].casefold(),d['id']))
    return rows[offset:offset+limit]
