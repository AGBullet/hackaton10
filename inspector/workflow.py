import collections,json,uuid
from .db import query,one,execute,audit,connection,params
from .comparison import revisions,compare_values,comparison_state,revision_key
from .extraction import scalar,document_pdf
from .registry import uid
from .catalog import catalog
from .protocols import validate_evidence
from .config import ROOT

def extracted_values(object_id):
    return query("""SELECT f.*,p.name AS parameter_name,d.name AS document_name,d.stage,d.revision,d.approval_status FROM facts f
        JOIN documents d ON d.id=f.file_id JOIN parameters p ON p.code=f.parameter_code
        JOIN pages pg ON pg.file_id=f.file_id AND pg.page=f.page AND pg.quality='GOOD'
        LEFT JOIN fact_validations v ON v.fact_id=f.id
        WHERE f.object_id=%s AND COALESCE(v.accepted,true) AND f.method NOT IN ('anchored','anchored-v2','anchored-v3') AND NOT(f.parameter_code='M-009' AND f.method='anchored-v4') AND d.parse_status!='ERROR'
        ORDER BY p.ordinal,d.stage,f.page LIMIT 500""",(object_id,))

def readiness(object_id):
    docs=query('SELECT id,name,stage,format,discipline,parse_status,page_count,parsed_pages,quality FROM documents WHERE object_id=%s',(object_id,))
    selected,issues=revisions(object_id);selected_ids={d['id'] for d in selected}
    stages={s:{'files':sum(d['stage']==s for d in docs),'viewable':sum(d['stage']==s and d['parse_status']!='UNSUPPORTED' for d in docs),'processed':sum(d['stage']==s and d['parsed_pages']>0 for d in docs),'selected':sum(d['stage']==s and d['id'] in selected_ids for d in docs)} for s in ('PD','RD','ID')}
    preferred={}
    for s in stages:
        ds=[d for d in docs if d['stage']==s and d['parse_status'] not in ('UNSUPPORTED','ERROR')]
        def score(d):
            return (d['id'] not in selected_ids,d['format']!='.pdf',d['discipline'] not in (('ПЗ','АР') if s=='PD' else ('АР','КЖ') if s=='RD' else ('АР','КЖ','КР')),not bool(d['parsed_pages']),d['name'])
        if ds:preferred[s]=min(ds,key=score)['id']
    pages=sum(d['parsed_pages'] for d in docs);facts=extracted_values(object_id)
    blockers=[]
    if not stages['PD']['files']:blockers.append('В комплекте объекта нет ПД — нет проектного эталона.')
    if not stages['RD']['files'] and not stages['ID']['files']:blockers.append('Нет РД или ИД для сравнения с ПД.')
    if not pages:blockers.append('Текст ещё не извлечён. Документы можно просматривать, но поиск и сравнение пока не имеют данных.')
    if not stages['PD']['selected'] or not (stages['RD']['selected'] or stages['ID']['selected']):blockers.append('Не выбран рабочий комплект редакций ПД и РД/ИД. Выберите документы в панелях и запустите проверку с основанием выбора.')
    if pages and not facts:blockers.append('Текст извлечён, но проверяемые значения пока не найдены. Нужны другие страницы, извлечение моделью или ручная сверка.')
    return {'stages':stages,'processed_pages':pages,'processed_files':sum(d['parsed_pages']>0 for d in docs),'total_files':len(docs),'known_total_pages':sum(d['page_count'] for d in docs),'fully_processed':sum(d['parse_status']=='PARSED' for d in docs),'low_quality_pages':sum(d['quality'].get('LOW_QUALITY',0) for d in docs),'values':len(facts),'selected_files':len(selected),'revision_issues':len(issues),'preferred_file_ids':preferred,'blockers':blockers}

def prepare_job(object_id,file_ids,reason,user,max_pages=25,use_model=True):
    if not reason.strip():raise ValueError('Нужно основание выбора рабочего комплекта')
    with connection() as c:
        obj=c.execute('SELECT * FROM objects WHERE id=%s FOR UPDATE',(object_id,)).fetchone()
        if not obj or obj['status']=='FINALIZED':raise ValueError('Объект не найден или проверка финализирована')
        if c.execute("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING')",(object_id,)).fetchone():raise ValueError('Обработка уже выполняется')
        docs=c.execute('SELECT * FROM documents WHERE object_id=%s AND id=ANY(%s)',(object_id,file_ids)).fetchall()
        if len(docs)!=len(set(file_ids)) or not docs:raise ValueError('Неверный набор документов')
        stages={d['stage'] for d in docs}
        if 'PD' not in stages or not stages.intersection({'RD','ID'}):raise ValueError('Выберите ПД и хотя бы один документ РД или ИД')
        keys=[revision_key(d) for d in docs]
        if len(set(keys))!=len(keys):raise ValueError('Не выбирайте одновременно разные редакции одного шифра')
        for d in docs:
            if d['parse_status']=='UNSUPPORTED':raise ValueError('Для сверки нужен поддерживаемый формат')
            c.execute('INSERT INTO revision_choices(object_id,stage,document_code,file_id,user_id,reason) VALUES(%s,%s,%s,%s,%s,%s)',(object_id,d['stage'],d['document_code'],d['id'],user,reason))
        job_id=str(uuid.uuid4())
        c.execute('INSERT INTO jobs(id,object_id,kind,payload) VALUES(%s,%s,%s,%s)',params((job_id,object_id,'workflow',{'file_ids':file_ids,'max_pages':max_pages,'ocr':True,'use_model':use_model,'page_budget':6})))
        c.execute('INSERT INTO audit(user_id,action,object_id,details) VALUES(%s,%s,%s,%s)',params((user,'working_set_selected',object_id,{'file_ids':file_ids,'reason':reason,'approval_metadata_changed':False})))
    return {'id':job_id,'status':'QUEUED'}

def manual_result(object_id,code,entity,evidence,reason,user):
    import fitz
    p=one('SELECT * FROM parameters WHERE code=%s',(code,))
    if not p:raise ValueError('Выберите параметр матрицы')
    if p['config']['scope']=='ELEMENT' and (not entity.strip() or entity=='OBJECT'):raise ValueError('Укажите конкретное помещение или элемент')
    if p['config']['scope']=='OBJECT':entity='OBJECT'
    enriched=[]
    for e in evidence:
        d=one('SELECT * FROM documents WHERE id=%s AND object_id=%s',(e.get('file_id'),object_id))
        if not d:raise ValueError('Источник принадлежит другому объекту')
        with fitz.open(document_pdf(d)) as pdf:count=len(pdf)
        execute('UPDATE documents SET page_count=%s WHERE id=%s',(count,d['id']))
        raw=e.get('raw','').strip()
        if not raw:raise ValueError('Введите ожидаемое и фактическое значение')
        enriched.append({**e,'sha256':d['sha256'],'stage':d['stage'],'document_code':d['document_code'],'revision':d['revision'],'value':scalar(raw,p['unit']),'entity':entity,'approval_status':d['approval_status'],'coordinate_space':'visible_rotated_page_normalized'})
    validate_evidence(enriched,object_id)
    expected=next(e['value'] for e in enriched if e['role']=='expected');actual=next(e['value'] for e in enriched if e['role']=='actual')
    state,delta=comparison_state(p,expected,actual)
    if state=='NOT_COMPARABLE':raise ValueError('Значения несопоставимы')
    fid=uid(object_id,'manual',str(uuid.uuid4()))
    with connection() as c:
        obj=c.execute('SELECT status FROM objects WHERE id=%s FOR UPDATE',(object_id,)).fetchone()
        if obj['status']=='FINALIZED':raise ValueError('Проверка финализирована')
        if c.execute("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING')",(object_id,)).fetchone():raise ValueError('Дождитесь завершения обработки')
        c.execute('INSERT INTO findings(id,object_id,parameter_code,rule_code,entity,machine_status,expected_value,actual_value,delta,evidence,rationale,priority,fingerprint,model_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',params((fid,object_id,code,'MANUAL_'+code,entity,state,expected,actual,delta,enriched,'Ручная сверка инспектора: '+reason,p['priority'],fid,'inspector-manual-v1')))
        c.execute('INSERT INTO audit(user_id,action,object_id,details) VALUES(%s,%s,%s,%s)',params((user,'manual_comparison',object_id,{'finding_id':fid,'reason':reason})))
    return {'id':fid,'status':state}

def quality_report():
    def read(name):
        p=ROOT/'reports'/name
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}
    benchmark=read('model_benchmark.json');ocr=read('ocr_benchmark.json')
    return {'thresholds':{'character_accuracy':.95,'key_fields_exact_match':.90,'document_link_accuracy':.95,'evidence_localization':.95,'precision':.90,'recall':.80,'f1':.85,'false_positive_rate_max':.10},
        'acceptance_established':False,'acceptance_note':'Нужен независимый GOLD по объектам. Малые технические тесты не являются приёмкой.',
        'models':{k:{key:value for key,value in v.items() if key!='cases'} for k,v in benchmark.get('models',{}).items()},
        'ocr_controlled_test':{k:v for k,v in ocr.items() if k not in ('samples','sha256','file_id')},
        'calls':query('SELECT model,purpose,count(*) AS calls,round(avg(seconds)::numeric,2) AS average_seconds,count(*) FILTER(WHERE valid) AS valid_json FROM model_calls GROUP BY model,purpose'),
        'expert_decisions':query('SELECT status,count(*) AS count FROM (SELECT DISTINCT ON(finding_id) status FROM reviews ORDER BY finding_id,id DESC) r GROUP BY status'),
        'current_development':read('20260924/metrics.json'),
        'source':'ТЗ 1.1, раздел 14; ответы организаторов №36–39'}
