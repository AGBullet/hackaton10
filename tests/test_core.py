import json, math
import pytest
import fitz
from inspector.comparison import choose_revisions,compare_values,findings,compare,affected_parameters
from inspector.extraction import extract_page,scalar,parse_document
from inspector.db import query,one,execute
from inspector.protocols import review,snapshot,build_snapshot,export_pdf,prepare_dataset,validate_evidence
from inspector.registry import register,create_object,uid
from scripts.unpack import safe_target

def d(id_,rev='1',date='2026-08-17',status='APPROVED',sha=None):
    return dict(id=id_,stage='PD',document_code='PZ',revision=rev,approval_status=status,approval_date=date,sha256=sha or id_,predecessor_id=None)

def test_revision_latest_approved_and_draft():
    selected,issues=choose_revisions([d('old'),d('new','2','2026-08-19'),d('draft','3','2026-08-20','DRAFT')])
    assert [x['id'] for x in selected]==['new'];assert not issues
def test_revision_conflict_abstains():
    selected,issues=choose_revisions([d('a'),d('b')]);assert not selected and issues[0]['status']=='CLARIFICATION_REQUIRED'
def test_revision_unknown_and_override():
    docs=[d('a',status='UNKNOWN')]
    assert not choose_revisions(docs)[0]
    selected,_=choose_revisions(docs,[dict(stage='PD',document_code='PZ',file_id='a')]);assert selected[0]['id']=='a'
def test_numeric_tolerances_and_units():
    assert compare_values(scalar('1 000','м²'),scalar('1 010','м²'),.01)[0] is False
    assert compare_values(scalar('1 000','м²'),scalar('1 011','м²'),.01)[0] is True
    assert compare_values(scalar('1','м'),scalar('1','мм'))[0] is None
    assert compare_values(scalar('0','м'),scalar('1','м'))[0] is True
def test_archive_traversal_and_windows_escape(tmp_path):
    for s in ['../evil','/abs','C:/evil','folder/../../evil']:
        with pytest.raises(ValueError):safe_target(tmp_path,s)
    assert '%3C' in str(safe_target(tmp_path,'документ <рот>.pdf'))

@pytest.mark.parametrize('angle',[0,90,180,270])
def test_rotated_cropbox_coordinates(pdf_factory,angle):
    p=pdf_factory('rotate.pdf',['Площадь застройки: 1000 м²'])
    doc=fitz.open(p);page=doc[0];page.set_cropbox(fitz.Rect(20,10,page.rect.width-20,page.rect.height-10));page.set_rotation(angle);out=p.with_name('cropped.pdf');doc.save(out);doc.close()
    r=extract_page(out,1,False);assert r['content']['rotation']==angle
    lines=[l for l in r['content']['lines'] if 'Площадь' in l['text']];assert lines
    b=lines[0]['bbox'];assert 0<=b[0]<b[2]<=1 and 0<=b[1]<b[3]<=1
    with fitz.open(out) as pdf:
        pg=pdf[0];rect=pg.search_for('Площадь')[0]*pg.rotation_matrix
        cx=(rect.x0+rect.x1)/2/pg.rect.width;cy=(rect.y0+rect.y1)/2/pg.rect.height
        assert b[0]<=cx<=b[2] and b[1]<=cy<=b[3]

def test_matrix_132_and_partial_rule_not_gold(compared_object):
    obj,docs=compared_object
    assert one('SELECT count(*) AS n FROM checks WHERE object_id=%s',(obj,))['n']==132
    fs=findings(obj);assert any(f['parameter_code']=='M-001' and f['status']=='CLARIFICATION_REQUIRED' for f in fs)
    assert all(f['status'] not in ('CONFIRMED_VIOLATION','NEGATIVE_VERIFIED') for f in fs)
    f=next(f for f in fs if f['parameter_code']=='M-001');assert f['expected_value']['normalized']==1000 and f['actual_value']['normalized']==950
    assert len(f['evidence'])==2
def test_missing_pd_no_candidate(isolated_db,pdf_factory):
    from inspector.extraction import extract_facts
    from inspector.catalog import catalog
    obj=create_object('NO-PD-'+uid(str(pdf_factory)))
    p=pdf_factory('rd.pdf',['Площадь застройки: 800 м²']);doc=register(p,obj,dict(stage='RD',revision='1',approval_status='APPROVED',approval_date='2026-08-17'))
    parse_document(doc['id'],allow_ocr=False);extract_facts(doc['id'],catalog());compare(obj)
    assert not findings(obj)
    assert one('SELECT finding_status FROM checks WHERE object_id=%s AND parameter_code=%s',(obj,'M-001'))['finding_status']=='MISSING_EVIDENCE'
def test_review_is_append_only_and_incremental_preserves(compared_object):
    obj,_=compared_object;f=next(f for f in findings(obj) if f['parameter_code']=='M-001')
    with pytest.raises(ValueError):review(f['id'],'CONFIRMED_VIOLATION','CONFIRMED','','test-inspector')
    review(f['id'],'CONFIRMED_VIOLATION','CONFIRMED','Проверены обе страницы, изменение не согласовано','test-inspector')
    compare(obj,['M-001'])
    assert next(x for x in findings(obj) if x['id']==f['id'])['status']=='CONFIRMED_VIOLATION'
    assert one('SELECT machine_status FROM findings WHERE id=%s',(f['id'],))['machine_status']=='CLARIFICATION_REQUIRED'
    assert one('SELECT count(*) AS n FROM reviews WHERE finding_id=%s',(f['id'],))['n']==1
def test_evidence_wrong_object_bbox_and_missing_source(compared_object):
    obj,_=compared_object;f=findings(obj)[0]
    with pytest.raises(ValueError):validate_evidence(f['evidence'][:1],obj)
    ev=json.loads(json.dumps(f['evidence']));ev[0]['bbox']=[0,0,2,1]
    with pytest.raises(ValueError):validate_evidence(ev,obj)
    ev=json.loads(json.dumps(f['evidence']));ev[0]['sha256']='0'*64
    with pytest.raises(ValueError):validate_evidence(ev,obj)
def test_finalize_with_clarification_freezes(compared_object):
    obj,_=compared_object
    for f in findings(obj):review(f['id'],'CLARIFICATION_REQUIRED','NEED_DOCUMENT','Нужно основание утверждения','test-inspector')
    p=snapshot(obj,True);assert p['status']=='PROTOCOL_FINALIZED'
    with pytest.raises(ValueError):review(findings(obj)[0]['id'],'NEGATIVE_VERIFIED','NO_DISCREPANCY','Проверено','test-inspector')
    with pytest.raises(ValueError):compare(obj)
def test_pdf_export_readable_russian(compared_object):
    obj,_=compared_object;data=build_snapshot(obj);pdf=export_pdf(data)
    with fitz.open(stream=pdf,filetype='pdf') as d:
        text=''.join(p.get_text() for p in d)
    assert 'Протокол сверки' in text and 'M-132' in text and 'Площадь застройки' in text
def test_cache_resume_pages(compared_object):
    _,docs=compared_object
    before=one('SELECT id,seconds FROM pages WHERE file_id=%s',(docs[0]['id'],))
    parse_document(docs[0]['id'],allow_ocr=False)
    after=one('SELECT id,seconds FROM pages WHERE file_id=%s',(docs[0]['id'],))
    assert before==after
def test_dataset_gate_never_trains_candidates(compared_object):
    result=prepare_dataset();assert result['training_allowed'] is False and result['publication_allowed'] is False

def test_http_boundaries_and_schema(isolated_db,monkeypatch):
    monkeypatch.setenv('ALLOW_REMOTE','0')
    from fastapi.testclient import TestClient
    from inspector.app import app
    with TestClient(app) as c:
        assert c.get('/api/health').status_code==200
        assert len(c.get('/api/parameters').json())==132
        assert c.post('/api/objects',headers={'Origin':'https://evil.example'},json={'name':'evil'}).status_code==403
        assert c.get('/api/health',headers={'Host':'evil.example'}).status_code==403
        assert c.post('/api/objects',json={'name':''}).status_code==422
        assert c.get('/').status_code==200
