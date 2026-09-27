import collections, datetime, hashlib, io, json, math
from xml.sax.saxutils import escape
from pathlib import Path
from .db import query,one,execute,audit,connection,params
from .comparison import findings,revisions,compare_values
from .config import MATRIX_VERSION,PARSER_VERSION,TEXT_MODEL,DATA,ROOT

def serial(value):return json.loads(json.dumps(value,ensure_ascii=False,default=str))

def completeness(object_id):
    obj=one('SELECT * FROM objects WHERE id=%s',(object_id,))
    docs=query('SELECT id,stage,document_code,sha256,parse_status FROM documents WHERE object_id=%s',(object_id,))
    manifest=obj.get('manifest') or {}
    expected=manifest.get('documents',[])
    result={}
    for stage in ('PD','RD','ID'):
        present=[d for d in docs if d['stage']==stage]
        required=[e for e in expected if e.get('stage')==stage and e.get('required',True)]
        missing=[e for e in required if not any((e.get('sha256') and e['sha256']==d['sha256']) or (e.get('document_code') and e['document_code']==d['document_code']) for d in present)]
        status=stage+'_MISSING' if not present else (stage+'_UPLOADED' if expected and not missing else stage+'_PARTIAL')
        result[stage]={'status':status,'uploaded':len(present),'expected':len(required) if expected else None,'missing':missing,'reason':None if expected else 'Ожидаемый манифест не задан; полнота не подтверждена'}
    stages={d['stage'] for d in docs if d['stage'] in ('PD','RD','ID')}
    scenario='FULL' if len(stages)==3 else ('PD_RD_ONLY' if stages=={'PD','RD'} else 'PD_ID_ONLY' if stages=={'PD','ID'} else 'RD_ID_ONLY' if stages=={'RD','ID'} else 'SINGLE_ONLY')
    working_complete=bool(expected) and all(not s['missing'] for s in result.values())
    return {'stages':result,'scenario':scenario,'manifest_provided':bool(expected),'working_set_complete':working_complete,'manifest_scope':manifest.get('scope','OBJECT'),'complete':working_complete and manifest.get('scope')!='DEMO_WORKING_SET_ONLY'}

def build_snapshot(object_id):
    obj=one('SELECT * FROM objects WHERE id=%s',(object_id,))
    if not obj:raise ValueError('Объект не найден')
    fs=findings(object_id)
    docs=query('SELECT id,name,sha256,stage,discipline,document_code,revision,approval_status,approval_date,predecessor_id,successor_id,parse_status,page_count,parsed_pages,quality FROM documents WHERE object_id=%s ORDER BY id',(object_id,))
    choices=query('SELECT * FROM revision_choices WHERE object_id=%s ORDER BY id',(object_id,))
    manifest_hash=hashlib.sha256(json.dumps(serial({'files':docs,'choices':choices,'applicability':obj['applicability'],'manifest':obj['manifest']}),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    checks=query('SELECT c.*,p.name,p.section,p.priority FROM checks c JOIN parameters p ON p.code=c.parameter_code WHERE c.object_id=%s ORDER BY p.ordinal',(object_id,))
    selected,issues=revisions(object_id)
    counts=collections.Counter(f['status'] for f in fs)
    return serial({'schema_version':'inspector-1.0','object':obj,'process_id':object_id+':'+str(obj['generation']),'run_timestamp':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'matrix_version':MATRIX_VERSION,'dataset_version':'local-inspector-v1','model_version':TEXT_MODEL+'/rules-v1','parser_version':PARSER_VERSION,'input_manifest_hash':manifest_hash,
        'completeness':completeness(object_id),'summary':{'parameters_total':132,'parameters_evaluated':len(checks),'confirmed_violations':counts['CONFIRMED_VIOLATION'],'candidates':counts['CANDIDATE'],'negative_verified':counts['NEGATIVE_VERIFIED'],'suspicions':counts['SUSPICION'],'status_counts':dict(counts)},
        'sections':{'completeness_and_comparability':checks,'candidates':[f for f in fs if f['status']=='CANDIDATE'],'confirmed_violations':[f for f in fs if f['status']=='CONFIRMED_VIOLATION'],'negative_verified':[f for f in fs if f['status']=='NEGATIVE_VERIFIED'],'suspicions':[f for f in fs if f['status']=='SUSPICION'],'requires_clarification':[f for f in fs if f['status']=='CLARIFICATION_REQUIRED'],'machine_nontriggers_pending_review':[f for f in fs if f['status']=='NO_TRIGGER_PENDING_REVIEW'],'machine_matches_pending_review':[f for f in fs if f['status']=='MATCH_PENDING_REVIEW']},
        'files':docs,'revision_choices':choices,'revision_issues':issues,'selected_file_ids':[d['id'] for d in selected],
        'limitations':['Автоматический результат не является решением инспектора.','Строительные нормы не проверяются; эталон — ПД.','Рукопись, неразмеченная геометрия и недостоверные фрагменты требуют экспертного рассмотрения.']})

def snapshot(object_id,finalize=False,user='inspector-local',job_id=None):
    with connection() as c:
        obj=c.execute('SELECT * FROM objects WHERE id=%s FOR UPDATE',(object_id,)).fetchone()
        if not obj:raise ValueError('Объект не найден')
        if job_id:
            previous=c.execute('SELECT id,version,status,snapshot FROM protocols WHERE job_id=%s',(job_id,)).fetchone()
            if previous:return previous
        if obj['status']=='FINALIZED':raise ValueError('Протокол уже финализирован')
        if finalize:
            pending=c.execute("SELECT count(*) AS n FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING')",(object_id,)).fetchone()['n']
            if pending:raise ValueError('Дождитесь завершения обработки')
        data=build_snapshot(object_id)
        if finalize and data['summary']['candidates']:raise ValueError('Обработайте всех кандидатов или перенесите их в уточнение')
        version=c.execute('SELECT COALESCE(max(version),0)+1 AS n FROM protocols WHERE object_id=%s',(object_id,)).fetchone()['n']
        status='PROTOCOL_FINALIZED' if finalize else 'PRELIMINARY'
        data.update(version=version,status=status,finalized_by=user if finalize else None)
        row=c.execute('INSERT INTO protocols(object_id,version,status,snapshot,manifest_hash,job_id) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id',params((object_id,version,status,data,data['input_manifest_hash'],job_id))).fetchone()
        if finalize:c.execute("UPDATE objects SET status='FINALIZED' WHERE id=%s",(object_id,))
        c.execute('INSERT INTO audit(user_id,action,object_id,details) VALUES(%s,%s,%s,%s)',params((user,'finalize' if finalize else 'protocol_version',object_id,{'version':version})))
    return dict(id=row['id'],version=version,status=status,snapshot=data)

def validate_evidence(evidence,object_id):
    if len(evidence)<2:raise ValueError('Нужны доказательства ПД и РД/ИД')
    stages=set();roles=set()
    for ev in evidence:
        required=('file_id','sha256','stage','page','bbox','quote','value','role')
        if any(k not in ev for k in required):raise ValueError('Неполная карточка доказательства')
        d=one('SELECT * FROM documents WHERE id=%s AND object_id=%s',(ev['file_id'],object_id))
        if not d or d['sha256']!=ev['sha256'] or d['stage']!=ev['stage']:raise ValueError('Источник не соответствует объекту/стадии/хешу')
        if not 1<=ev['page']<=d['page_count']:raise ValueError('Страница вне документа')
        box=ev['bbox']
        if len(box)!=4 or not all(isinstance(v,(int,float)) and math.isfinite(v) and 0<=v<=1 for v in box) or box[0]>=box[2] or box[1]>=box[3]:raise ValueError('Неверные координаты доказательства')
        if not ev['quote'].strip():raise ValueError('Пустое доказательство')
        if ev['role']=='expected' and ev['stage']!='PD':raise ValueError('Ожидаемое значение должно ссылаться на ПД')
        if ev['role']=='actual' and ev['stage'] not in ('RD','ID'):raise ValueError('Фактическое значение должно ссылаться на РД/ИД')
        if not isinstance(ev['value'],dict) or not all(k in ev['value'] for k in ('raw','normalized','unit','kind')):raise ValueError('Значение не прошло проверку структуры')
        value=ev['value']
        if value['kind'] not in ('number','text') or not isinstance(value['raw'],str) or not isinstance(value['unit'],str):raise ValueError('Недопустимый тип значения')
        if value['kind']=='number' and (not isinstance(value['normalized'],(int,float)) or isinstance(value['normalized'],bool) or not math.isfinite(value['normalized'])):raise ValueError('Числовое значение должно быть конечным числом')
        if value['kind']=='text' and (not isinstance(value['normalized'],str) or not value['normalized'].strip()):raise ValueError('Текстовое значение пусто')
        if str(ev['value']['raw']).casefold().replace(' ','') not in ev['quote'].casefold().replace(' ',''):raise ValueError('Значение отсутствует в цитате доказательства')
        stages.add(ev['stage']);roles.add(ev['role'])
    if 'PD' not in stages or not stages.intersection({'RD','ID'}) or not {'expected','actual'}.issubset(roles):raise ValueError('Нужны ожидаемое из ПД и фактическое из РД/ИД')
    selected,_=revisions(object_id)
    if not {e['file_id'] for e in evidence}.issubset({d['id'] for d in selected}):raise ValueError('Источники не являются выбранными актуальными редакциями')
    return True

REASONS={'CONFIRMED','OCR_ERROR','WRONG_REVISION','APPROVED_CHANGE','WRONG_LINK','NOT_APPLICABLE','NO_DISCREPANCY','NEED_DOCUMENT','OTHER'}

def review(finding_id,status,reason,comment,user,expert_verified=False,training_consent=False):
    if status not in ('CONFIRMED_VIOLATION','NEGATIVE_VERIFIED','CLARIFICATION_REQUIRED','CANDIDATE'):raise ValueError('Недопустимое решение')
    if reason not in REASONS or not comment.strip() or not user.strip():raise ValueError('Укажите причину, комментарий и инспектора')
    f=one('SELECT * FROM findings WHERE id=%s',(finding_id,))
    if not f or not f['active']:raise ValueError('Кандидат устарел или не найден')
    with connection() as c:
        obj=c.execute('SELECT * FROM objects WHERE id=%s FOR UPDATE',(f['object_id'],)).fetchone()
        if obj['status']=='FINALIZED':raise ValueError('Финализированный протокол нельзя изменять')
        if c.execute("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING') LIMIT 1",(f['object_id'],)).fetchone():raise ValueError('Дождитесь завершения обработки перед решением инспектора')
        prev=c.execute('SELECT status FROM reviews WHERE finding_id=%s ORDER BY id DESC LIMIT 1',(finding_id,)).fetchone()
        current=prev['status'] if prev else f['machine_status']
        if current=='SUSPICION' and status=='CONFIRMED_VIOLATION':raise ValueError('Сначала привяжите доказательства и переведите гипотезу в кандидата')
        override=c.execute('SELECT evidence FROM evidence_overrides WHERE finding_id=%s ORDER BY id DESC LIMIT 1',(finding_id,)).fetchone()
        ev=override['evidence'] if override else f['evidence']
        if status=='CONFIRMED_VIOLATION':
            expected=next((e['value'] for e in ev if e.get('role')=='expected'),None)
            actual=next((e['value'] for e in ev if e.get('role')=='actual'),None)
            if expected and actual and compare_values(expected,actual)[0] is False:
                raise ValueError('Значения совпадают: подтвердите совпадение. Если источник прочитан неверно, сначала исправьте доказательства.')
        if status!='CLARIFICATION_REQUIRED':validate_evidence(ev,f['object_id'])
        c.execute('INSERT INTO reviews(finding_id,status,reason_code,comment,user_id,evidence_snapshot,expert_verified,training_consent,label_source) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)',params((finding_id,status,reason,comment,user,ev,expert_verified,training_consent,'EXPERT_ATTESTED' if expert_verified else 'UNVERIFIED_LOCAL_REVIEW')))
        c.execute("UPDATE objects SET status='VERIFYING' WHERE id=%s",(f['object_id'],))
        c.execute('INSERT INTO audit(user_id,action,object_id,details) VALUES(%s,%s,%s,%s)',params((user,'review',f['object_id'],{'finding_id':finding_id,'status':status,'reason':reason,'comment':comment})))
    return {'status':status,'finding_id':finding_id}

def correct_evidence(finding_id,evidence,reason,user):
    finding=one('SELECT object_id FROM findings WHERE id=%s AND active',(finding_id,))
    if not finding:raise ValueError('Находка не найдена или устарела')
    with connection() as c:
        obj=c.execute('SELECT status FROM objects WHERE id=%s FOR UPDATE',(finding['object_id'],)).fetchone()
        if obj['status']=='FINALIZED':raise ValueError('Финализированный протокол нельзя изменять')
        if c.execute("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING')",(finding['object_id'],)).fetchone():raise ValueError('Дождитесь завершения обработки')
        validate_evidence(evidence,finding['object_id'])
        c.execute('INSERT INTO evidence_overrides(finding_id,evidence,reason,user_id) VALUES(%s,%s,%s,%s)',params((finding_id,evidence,reason,user)))
        c.execute("INSERT INTO reviews(finding_id,status,reason_code,comment,user_id,evidence_snapshot) VALUES(%s,'CLARIFICATION_REQUIRED','OTHER',%s,%s,%s)",params((finding_id,reason,user,evidence)))
        c.execute("UPDATE objects SET status='VERIFYING' WHERE id=%s",(finding['object_id'],))
        c.execute('INSERT INTO audit(user_id,action,object_id,details) VALUES(%s,%s,%s,%s)',params((user,'evidence_correction',finding['object_id'],{'finding_id':finding_id,'reason':reason})))
    return {'status':'CLARIFICATION_REQUIRED','finding_id':finding_id}

def export_pdf(data):
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib import colors
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font=Path('C:/Windows/Fonts/arial.ttf')
    if not font.exists():font=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if 'Inspector' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('Inspector',str(font)))
    styles=getSampleStyleSheet()
    for style in styles.byName.values():style.fontName='Inspector'
    styles['BodyText'].fontSize=8;styles['BodyText'].leading=11
    out=io.BytesIO();doc=SimpleDocTemplate(out,pagesize=(842,595),leftMargin=30,rightMargin=30,topMargin=30,bottomMargin=30)
    story=[]
    def para(text,style='BodyText'):return Paragraph(escape(str(text)).replace('\n','<br/>'),styles[style])
    story+=[para('Инспектор ИИ · Протокол сверки','Title'),para(data['object']['name'],'Heading2'),para(f"Версия: {data.get('version','предпросмотр')} · {data.get('status','PRELIMINARY')} · {data['run_timestamp']}"),para('ПД — эталон. Подтверждение нарушений выполняет инспектор.'),Spacer(1,12)]
    story += [para('1. Статус загрузки и полнота','Heading2')]
    for stage,s in data['completeness']['stages'].items():story.append(para(f"{stage}: {s['status']}; загружено {s['uploaded']}; ожидается {s['expected'] if s['expected'] is not None else 'не задано'}. {s.get('reason') or ''}"))
    story.append(para('2. Сводка','Heading2'));story.append(para(f"Параметров: {data['summary']['parameters_total']}; зарегистрировано проверок: {data['summary']['parameters_evaluated']}; кандидатов: {data['summary']['candidates']}; подтверждено инспектором: {data['summary']['confirmed_violations']}. Отсутствие доказательств не означает соответствия."))
    titles=[('candidates','3. Предварительные кандидаты'),('confirmed_violations','4. Подтверждённые инспектором нарушения'),('negative_verified','5. Проверенные отрицательные результаты'),('suspicions','6. Гипотезы свободного поиска'),('requires_clarification','7. Требуют уточнения'),('machine_matches_pending_review','8. Машинные совпадения, не являющиеся GOLD')]
    titles.append(('machine_nontriggers_pending_review','8.1. Отличия без срабатывания направленного правила; требуют проверки'))
    for key,title in titles:
        story.append(para(title,'Heading2'))
        for f in data['sections'].get(key,[]):
            story.append(para(f"{f.get('parameter_code') or f['rule_code']} · {f['entity']} · {f['status']} · ID {f['id']}",'Heading3'))
            expected=f['expected_value'];actual=f['actual_value']
            def shown(v):return v.get('normalized') if v.get('source_unit') and v['source_unit']!=v['unit'] else v.get('raw','—')
            story.append(para(f"Ожидаемое: {shown(expected)} {expected.get('unit','')} / Фактическое: {shown(actual)} {actual.get('unit','')}"));story.append(para(f['rationale']))
            for ev in f.get('current_evidence',f['evidence']):
                story.append(para(f"{ev['stage']} · файл {ev['file_id']} · шифр {ev.get('document_code')} · ред. {ev.get('revision')} · статус {ev.get('approval_status')} · стр. {ev['page']} · область {ev['bbox']}\nSHA-256 {ev['sha256']}\n{ev['quote']}"))
            story.append(para(f"Инспектор: {f.get('user_id') or '—'}; причина: {f.get('reason_code') or '—'}; комментарий: {f.get('comment') or '—'}"))
        if not data['sections'].get(key):story.append(para('Нет записей'))
    story.append(para('9. Покрытие матрицы и недостающие доказательства','Heading2'))
    rows=[[para(x) for x in ['Код','Параметр','Реализация','Комплектность / результат']]]
    for c in data['sections']['completeness_and_comparability']:rows.append([para(c['parameter_code']),para(c['name']),para(c['implementation_status']),para(c['completeness_status']+' / '+c['finding_status'])])
    table=Table(rows,colWidths=[52,330,150,250],repeatRows=1);table.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e9edf3')),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#ccd2da'))]));story.append(table)
    story.append(para('10. Трассируемость','Heading2'));story.append(para(f"Матрица {data['matrix_version']}; модели {data['model_version']}; набор {data['dataset_version']}; process_id {data['process_id']}; SHA-256 манифеста {data['input_manifest_hash']}"))
    for limitation in data['limitations']:story.append(para(limitation))
    doc.build(story);return out.getvalue()

def prepare_dataset():
    from .provenance import public_source_hashes
    allowed=public_source_hashes()
    records=query('''SELECT f.id,f.object_id,f.parameter_code,f.entity,f.expected_value,f.actual_value,r.*
    FROM findings f JOIN LATERAL (SELECT * FROM reviews WHERE finding_id=f.id ORDER BY id DESC LIMIT 1) r ON true
    WHERE f.active AND r.status IN ('CONFIRMED_VIOLATION','NEGATIVE_VERIFIED') ORDER BY f.object_id,f.id''')
    valid=[];excluded=[]
    for r in records:
        try:
            if not r['expert_verified'] or not r['training_consent']:raise ValueError('Нет явной экспертной проверки или разрешения на обучение')
            if not all(e.get('sha256') in allowed for e in r['evidence_snapshot']):raise ValueError('Источник не включён в разрешённый TRAIN_PUBLIC по SHA-256')
            validate_evidence(r['evidence_snapshot'],r['object_id'])
            # Corrected, attested evidence is the training target; machine outputs stay immutable.
            r['expected_value']=next(e['value'] for e in r['evidence_snapshot'] if e['role']=='expected')
            r['actual_value']=next(e['value'] for e in r['evidence_snapshot'] if e['role']=='actual')
            valid.append(serial(r))
        except Exception as e:excluded.append({'finding_id':r['finding_id'],'reason':str(e)})
    objects=sorted({r['object_id'] for r in valid})
    parent={o:o for o in objects}
    def root(o):
        while parent[o]!=o:o=parent[o]
        return o
    owners={}
    for r in valid:
        for e in r['evidence_snapshot']:
            h=e['sha256']
            if h in owners:parent[root(r['object_id'])]=root(owners[h])
            else:owners[h]=r['object_id']
    groups=sorted({root(o) for o in objects},key=lambda o:hashlib.sha256(o.encode()).hexdigest())
    group_split={o:('train' if i<max(1,int(len(groups)*.6)) else 'validation' if i<max(2,int(len(groups)*.8)) else 'test') for i,o in enumerate(groups)}
    for r in valid:r['split']=group_split[root(r['object_id'])];r['object_group_id']=root(r['object_id'])
    labels=collections.Counter(r['status'] for r in valid)
    eligible=len(groups)>=5 and labels['CONFIRMED_VIOLATION']>=30 and labels['NEGATIVE_VERIFIED']>=30 and {r['split'] for r in valid}=={'train','validation','test'}
    content='\n'.join(json.dumps(r,ensure_ascii=False,sort_keys=True) for r in valid)
    digest=hashlib.sha256(content.encode()).hexdigest();dest=DATA/'exports'/('dataset-'+digest[:12]);dest.mkdir(exist_ok=True)
    (dest/'verified.jsonl').write_text(content,encoding='utf-8')
    meta={'dataset_version':digest[:12],'sha256':digest,'records':len(valid),'labels':dict(labels),'object_groups':len(groups),'split_counts':dict(collections.Counter(r['split'] for r in valid)),'training_allowed':eligible,'publication_allowed':False,'gate':'Минимум 30 положительных, 30 отрицательных, 5 независимых групп объектов; затем оценка и ручное разрешение публикации. Порог пилотный, не гарантия достаточности.','excluded':excluded,'path':str(dest.relative_to(ROOT))}
    (dest/'manifest.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    execute('INSERT INTO datasets(id,metadata) VALUES(%s,%s) ON CONFLICT(id) DO NOTHING',(digest,meta));return meta
