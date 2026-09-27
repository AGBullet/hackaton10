from inspector.metrics import evaluate,ocr_metrics,iou

def test_ocr_weighted_cer_and_nfc():
    r=ocr_metrics([('Привет','Привeт'),('е\u0308','ё')]);assert r['reference_characters']==7 and r['cer']==1/7
def test_evidence_wrong_page_is_not_true_positive():
    e={'sha256':'a','page':1,'stage':'PD','bbox':[0,0,.5,.5]}
    g={'object_id':'o','parameter_code':'M-001','entity':'OBJECT','status':'CONFIRMED_VIOLATION','evidence':[e]}
    p=dict(g,status='CANDIDATE',evidence=[dict(e,page=2)])
    r=evaluate([g],[p]);assert r['tp']==0 and r['fp']==1 and r['fn']==1
    p['evidence']=[e];r=evaluate([g],[p]);assert r['f1']==1
def test_candidates_in_gold_excluded():
    r=evaluate([{'status':'CANDIDATE'}],[]);assert r['gold_groups']==0 and r['gold_excluded_unverified']==1
