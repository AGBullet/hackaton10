import copy,uuid
import pytest
from inspector.db import execute,one,query
from inspector.comparison import compare,findings
from inspector.workflow import prepare_job,manual_result

def test_role_permissions_enforced_by_api(isolated_db,monkeypatch):
    from fastapi.testclient import TestClient
    from inspector.app import app
    monkeypatch.setenv('APP_REQUIRE_ROLE_PASSWORD','0')
    with TestClient(app) as c:
        assert c.get('/api/session').json()['role']=='inspector'
        assert c.get('/api/quality').status_code==403
        assert c.post('/api/scan').status_code==403
        assert c.post('/api/session',json={'role':'ml_engineer'}).status_code==200
        assert c.get('/api/quality').status_code==200
        assert c.post('/api/objects',json={'name':'Forbidden'}).status_code==403
        assert c.post('/api/findings/not-found/review',json={}).status_code==403
        assert c.post('/api/session',json={'role':'admin'}).status_code==200
        assert c.get('/api/quality').status_code==403
        assert c.post('/api/documents/not-found/vision',json={}).status_code==403

def test_provisional_sources_then_explicit_workflow(compared_object):
    from inspector.worker import process
    from inspector.protocols import review
    obj,docs=compared_object
    execute("UPDATE documents SET approval_status='UNKNOWN',approval_date=NULL WHERE object_id=%s",(obj,))
    compare(obj)
    provisional=findings(obj)
    assert provisional and all(f['status']=='CLARIFICATION_REQUIRED' for f in provisional)
    with pytest.raises(ValueError):review(provisional[0]['id'],'CONFIRMED_VIOLATION','CONFIRMED','Нельзя без редакций','test')
    prepared=prepare_job(obj,[d['id'] for d in docs],'Тестовый рабочий комплект','test',1,False)
    job=one('SELECT * FROM jobs WHERE id=%s',(prepared['id'],))
    result=process(job)
    execute("UPDATE jobs SET status='DONE',result=%s WHERE id=%s",(result,prepared['id']))
    actual=findings(obj)
    assert actual and any(f['status']=='CLARIFICATION_REQUIRED' for f in actual)
    assert not any(f['model_version']=='provisional-v1' for f in actual)
    assert not query('SELECT r.* FROM reviews r JOIN findings f ON f.id=r.finding_id WHERE f.object_id=%s',(obj,))
    assert all(d['approval_status']=='UNKNOWN' for d in query('SELECT approval_status FROM documents WHERE object_id=%s',(obj,)))

def test_manual_candidate_requires_evidence_and_stays_separate(compared_object):
    obj,_=compared_object;f=next(f for f in findings(obj) if f['parameter_code']=='M-001')
    evidence=[{**e,'raw':e['value']['raw']} for e in copy.deepcopy(f['evidence'])]
    result=manual_result(obj,'M-001','OBJECT',evidence,'Сверено визуально','test')
    assert result['status']=='CANDIDATE'
    compare(obj)
    assert any(f['id']==result['id'] for f in findings(obj))
    bad=copy.deepcopy(evidence);bad[1]['raw']='999999'
    with pytest.raises(ValueError):manual_result(obj,'M-001','OBJECT',bad,'Неверное значение','test')

def test_page_budget_continues_after_cached_pages(isolated_db,pdf_factory):
    import fitz
    from inspector.registry import register,create_object
    from inspector.extraction import parse_document
    path=pdf_factory('continue.pdf',['Площадь застройки 1000'])
    with fitz.open(path) as doc:
        doc.new_page();doc.new_page();doc.saveIncr()
    obj=create_object('RESUME-'+uuid.uuid4().hex)
    d=register(path,obj,{'stage':'PD'})
    assert parse_document(d['id'],max_pages=1,allow_ocr=False)['processed']==1
    assert parse_document(d['id'],max_pages=1,allow_ocr=False)['processed']==2
    assert parse_document(d['id'],max_pages=1,allow_ocr=False)['processed']==3

def test_fire_class_not_number_and_wrong_altitude_excluded(isolated_db,pdf_factory):
    from inspector.registry import register,create_object
    from inspector.extraction import parse_document,extract_facts,scalar
    from inspector.catalog import catalog
    obj=create_object('TYPED-'+uuid.uuid4().hex)
    d=register(pdf_factory('classes.pdf',['Класс конструктивной пожарной опасности С0','Абсолютная отметка 0.000 163,46','Верхняя абсолютная отметка 0.000 200']),obj,{'stage':'PD'})
    parse_document(d['id'],allow_ocr=False);extract_facts(d['id'],catalog())
    facts=query("SELECT * FROM facts WHERE file_id=%s AND method='anchored-v4'",(d['id'],))
    assert any(f['parameter_code']=='M-023' and f['value']['normalized']=='C0' for f in facts)
    assert not any(f['parameter_code']=='M-009' and f['value']['normalized']==200 for f in facts)
    assert scalar('В35','')['normalized']==scalar('B35','')['normalized']

def test_llm_literal_grounding_does_not_accept_geology_as_building_datum():
    from inspector.comparison import parameter_semantics_valid
    from inspector.extraction import scalar
    p={'ordinal':9}
    assert not parameter_semantics_valid(p,'Абсолютные отметки подошвы слоя от 131.60 до 132.62 м.',scalar('131.60','м'))
    assert not parameter_semantics_valid(p,'За отметку 0,000 принята абсолютная отметка 163,46.',scalar('0,000','м'))
    assert parameter_semantics_valid(p,'За отметку 0,000 принята абсолютная отметка 163,46.',scalar('163,46','м'))
