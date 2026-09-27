import pytest,copy
from inspector.db import one,query
from inspector.registry import register,create_object,uid
from inspector.extraction import parse_document,extract_facts
from inspector.catalog import catalog
from inspector.comparison import findings
from inspector.protocols import validate_evidence

@pytest.mark.parametrize('angle',[0,90,180,270])
def test_vlm_region_preserves_rotated_visible_coordinates(pdf_factory,angle):
    import fitz
    from inspector.extraction import render_region
    p=pdf_factory('region.pdf',['Площадь застройки 1225,8'])
    with fitz.open(p) as pdf:
        page=pdf[0];page.set_rotation(angle)
        rect=page.search_for('Площадь застройки')[0]*page.rotation_matrix
        box=[rect.x0/page.rect.width,rect.y0/page.rect.height,rect.x1/page.rect.width,rect.y1/page.rect.height]
        region=render_region(page,box,scale_limit=2)
        full=page.get_pixmap(matrix=fitz.Matrix(2,2),alpha=False)
        reference=fitz.Pixmap(full.colorspace,region.irect,False)
        reference.copy(full,region.irect)
        assert region.width>0 and region.height>0
        assert region.samples==reference.samples
        assert min(region.samples)<100

def test_source_mutation_invalidates_cache(compared_object):
    from inspector.config import ROOT
    _,docs=compared_object
    d=docs[0];p=ROOT/d['path'];p.write_bytes(p.read_bytes()+b'\n% changed source')
    with pytest.raises(ValueError,match='SOURCE_CHANGED'):parse_document(d['id'],allow_ocr=False)
    assert one('SELECT parse_status FROM documents WHERE id=%s',(d['id'],))['parse_status']=='ERROR'
    from inspector.comparison import compare,revisions,search
    assert d['id'] not in [s['id'] for s in revisions(d['object_id'])[0]]
    assert not search(d['object_id'],'площадь',file_id=d['id'])
    compare(d['object_id'],['M-001'])
    assert not [f for f in findings(d['object_id']) if f['parameter_code']=='M-001']

def test_evidence_roles_and_literal_validation(compared_object):
    import copy
    obj,_=compared_object;ev=copy.deepcopy(findings(obj)[0]['evidence'])
    ev[0]['role']='actual'
    with pytest.raises(ValueError):validate_evidence(ev,obj)
    ev=copy.deepcopy(findings(obj)[0]['evidence']);ev[0]['value'].update(kind='number',normalized=float('inf'))
    with pytest.raises(ValueError):validate_evidence(ev,obj)

def test_free_hypothesis_requires_promotion(compared_object,monkeypatch):
    import json
    import inspector.comparison as cmp
    from inspector.models import HypothesisReply
    from inspector.protocols import review
    obj,_=compared_object
    def fake(prompt,*args,**kwargs):
        fragments=json.loads(prompt)['fragments']
        a=next(f for f in fragments if f['stage']=='PD' and '1000' in f['text'])
        b=next(f for f in fragments if f['stage']=='RD' and '950' in f['text'])
        return HypothesisReply(hypotheses=[dict(expected_ref=a['id'],actual_ref=b['id'],expected_value='1000',actual_value='950',entity='OBJECT',description='Различается площадь')])
    monkeypatch.setattr(cmp,'call_model',fake)
    assert cmp.free_search(obj,'площадь')['created']==1
    f=next(f for f in findings(obj) if f['status']=='SUSPICION')
    with pytest.raises(ValueError):review(f['id'],'CONFIRMED_VIOLATION','CONFIRMED','Проверено','test')
    review(f['id'],'CANDIDATE','CONFIRMED','Источники привязаны и проверены','test')
    review(f['id'],'CONFIRMED_VIOLATION','CONFIRMED','Обе страницы проверены','test')
    assert next(x for x in findings(obj) if x['id']==f['id'])['status']=='CONFIRMED_VIOLATION'
    ev=copy.deepcopy(findings(obj)[0]['evidence']);ev[0]['value']['raw']='12345678'
    with pytest.raises(ValueError):validate_evidence(ev,obj)

def test_real_pd_table_regression(isolated_db):
    from inspector.config import ROOT
    p=next((ROOT/'_extracted/3a586bb2f074b6df').rglob('НВС-2025.03-1.2-ПЗ.pdf'),None)
    if p is None:pytest.skip('Дополнительный корпус не входит в публичный репозиторий')
    obj=create_object('REAL-TABLE-REGRESSION-'+uid(str(p)))
    d=register(p,obj,{'stage':'PD'})
    parse_document(d['id'],max_pages=10,allow_ocr=False);extract_facts(d['id'],catalog())
    facts=query("SELECT parameter_code,value FROM facts WHERE file_id=%s AND method='anchored-v4'",(d['id'],))
    assert any(f['parameter_code']=='M-001' and f['value']['normalized']==1225.8 for f in facts)
    assert not any(f['parameter_code']=='M-007' and f['value']['normalized'] in (1,3) for f in facts)
