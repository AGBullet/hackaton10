import collections, datetime, hashlib, json, re, time
from .db import query,one,execute,audit
from .registry import uid
from .extraction import norm,scalar,extract_facts,NUMBER,datum_evidence,object_label_valid,literal_unit
from .catalog import catalog
from .models import call_model,ExtractionReply,NumericExtractionReply,HypothesisReply
from .config import TEXT_MODEL

def revision_key(document):
    # A split album may contain different sheets with the same printed code.
    # They are different parts, not competing revisions of one file.
    return (document['stage'],document['document_code'] or document['id'],str((document.get('metadata') or {}).get('sheet') or ''))

def choose_revisions(documents,choices=()):
    groups=collections.defaultdict(list)
    for d in documents:
        groups[revision_key(d)].append(d)
    overrides={}
    documents_by_id={d['id']:d for d in documents}
    for c in choices:
        chosen=documents_by_id.get(c['file_id'])
        if chosen:overrides[revision_key(chosen)]=c['file_id']
    selected=[];issues=[]
    for key,ds in groups.items():
        override=overrides.get(key)
        if override:
            found=next((d for d in ds if d['id']==override),None)
            if found:selected.append(found);continue
        valid=[d for d in ds if d['approval_status'] in ('APPROVED','FOR_CONSTRUCTION') and d['approval_date'] and d['revision'] is not None and d['stage'] in ('PD','RD','ID')]
        if not valid:
            issues.append({'stage':key[0],'document_code':key[1],'status':'CLARIFICATION_REQUIRED','reason':'Нет подтверждённой редакции, даты утверждения или стадии','file_ids':[d['id'] for d in ds]});continue
        # Explicit successor links remove predecessor documents; dates resolve remaining revisions.
        superseded={d.get('predecessor_id') for d in valid if d.get('predecessor_id')}
        valid=[d for d in valid if d['id'] not in superseded]
        latest=max(d['approval_date'] for d in valid)
        winners=[d for d in valid if d['approval_date']==latest]
        if len({d['sha256'] for d in winners})!=1:
            issues.append({'stage':key[0],'document_code':key[1],'status':'CLARIFICATION_REQUIRED','reason':'Несколько утверждённых редакций с одинаковой датой','file_ids':[d['id'] for d in winners]});continue
        selected.append(winners[0])
    return selected,issues

def revisions(object_id):
    docs=query('SELECT * FROM documents WHERE object_id=%s',(object_id,))
    invalid=[d for d in docs if d['parse_status']=='ERROR']
    selected,issues=choose_revisions([d for d in docs if d['parse_status']!='ERROR'],query('SELECT * FROM revision_choices WHERE object_id=%s ORDER BY id',(object_id,)))
    issues.extend({'stage':d['stage'],'document_code':d['document_code'],'status':'SOURCE_ERROR','reason':str(d['quality']),'file_ids':[d['id']]} for d in invalid)
    return selected,issues

def search(object_id,q,stage=None,file_id=None,revision=None,entity=None,current_only=False,limit=30,quality=None):
    sql='''SELECT p.id,p.file_id,p.page,p.text,p.quality,p.content,d.name,d.stage,d.revision,d.document_code,d.approval_status,d.sha256
    FROM pages p JOIN documents d ON d.id=p.file_id WHERE d.object_id=%s AND d.parse_status!='ERROR' '''
    args=[object_id]
    if quality:
        sql+=' AND p.quality=%s';args.append(quality)
    if q:
        sql+=" AND (p.search_vector @@ websearch_to_tsquery('russian',%s) OR p.text ILIKE %s)";args.extend([q,'%'+q+'%'])
    for col,val in [('stage',stage),('id',file_id),('revision',revision)]:
        if val:sql+=f' AND d.{col}=%s';args.append(val)
    if entity:
        sql+=' AND p.text ~* %s';args.append('(^|[^[:alnum:]])'+re.escape(entity)+'($|[^[:alnum:]])')
    if current_only:
        selected,_=revisions(object_id)
        ids=[d['id'] for d in selected]
        if not ids:return []
        sql+=' AND d.id=ANY(%s::text[])';args.append('{'+','.join(ids)+'}')
    if q:
        sql+=" ORDER BY ts_rank_cd(p.search_vector,websearch_to_tsquery('russian',%s)) DESC,d.stage,p.page LIMIT %s";args.extend([q,limit])
    else:sql+=' ORDER BY d.stage,p.page LIMIT %s';args.append(limit)
    rows=query(sql,args)
    for r in rows:
        r['lines']=r.pop('content')['lines'];r['text']=r['text'][:12000]
    return rows

def context_lines(lines,terms,budget=7000):
    hits=[i for i,l in enumerate(lines) if any(t.casefold() in l['text'].casefold() for t in terms)]
    indices=sorted({j for i in hits for j in range(max(0,i-4),min(len(lines),i+6))}) if hits else list(range(min(100,len(lines))))
    result=[];used=0
    for i in indices:
        cost=len(lines[i]['text'])+85
        if used+cost>budget:continue
        result.append(lines[i]);used+=cost
    return result

def parameter_semantics_valid(parameter,quote,value):
    """Literal grounding alone cannot distinguish a building datum from geology."""
    text=norm(quote).casefold()
    if not object_label_valid(parameter['ordinal'],quote):return False
    if parameter['ordinal']==9:
        if not re.search(r'0[.,]0{2,3}(?!\d)|нулев\w*\s+отмет|отмет\w*\s+чист\w*\s+пол',text):return False
        if value['kind']!='number':return False
        # A relative zero is the label, not the absolute elevation being extracted.
        if value['normalized']==0 and re.search(r'абсолютн\w*\s+отмет\w*\s+[1-9]',text):return False
        relationship=datum_evidence({'text':quote,'bbox':[0,0,1,1]},[])
        if not relationship or scalar(relationship[0],parameter.get('unit',value['unit']))['normalized']!=value['normalized']:return False
    if parameter['ordinal']==23 and not re.fullmatch(r'C[0-3]',str(value['normalized'])):return False
    return True

def normalize_model_value(raw,unit,expected_unit):
    """Remove only a complete, recognized unit suffix/punctuation; never pick a number from prose."""
    clean_unit=norm(unit).rstrip('.')
    if clean_unit!=expected_unit.rstrip('.'):raise ValueError('Единицы модели не соответствуют параметру')
    value=norm(raw).strip()
    match=re.fullmatch(r'('+NUMBER+r')\s*(?:'+re.escape(expected_unit.rstrip('.'))+r')?\.?',value)
    return match.group(1) if match else value

def parameter_context(lines,parameter,budget=5000):
    if parameter['ordinal']!=9:return context_lines(lines,parameter['config']['aliases'],budget)
    hits=[l for l in lines if re.search(r'0[.,]0{2,3}(?!\d)|нулев\w*\s+отмет',l['text'],re.I)]
    if not hits:return context_lines(lines,['абсолют'],min(2500,budget))
    selected=list(hits)
    for hit in hits:
        b=hit['bbox'];height=b[3]-b[1]
        for l in lines:
            r=l['bbox']
            if abs(r[1]-b[1])<height*5 and abs(r[0]-b[0])<.03 and l not in selected:selected.append(l)
    output=[];size=0
    for l in selected:
        cost=len(l['text'])+85
        if size+cost<=budget:output.append(l);size+=cost
    return output

def assisted_extract(object_id,parameter_codes=None,page_budget=12,callback=None,file_ids=None,include_unselected=False,cancel=None):
    ps=[p for p in catalog() if not parameter_codes or p['code'] in parameter_codes]
    selected,issues=revisions(object_id)
    allowed={d['id']:d for d in selected}
    if include_unselected:
        allowed={d['id']:d for d in query("SELECT * FROM documents WHERE object_id=%s AND parse_status NOT IN ('ERROR','UNSUPPORTED')",(object_id,))}
    processed=0;added=0;rejected=0;attempted=set()
    for p in ps:
        if cancel and cancel():break
        if processed>=page_budget:break
        attempted.add(p['code'])
        before_processed=processed;before_added=added
        # Use parameter name / aliases only. No organizer labels or matrix_codes from manifests.
        terms=re.findall(r'[а-яА-Я]{4,}',p['name'])[:4]
        q=' OR '.join(terms)
        pages=[]
        for stage in ('PD','RD','ID'):
            stage_pages=search(object_id,p['config']['aliases'][0],stage=stage,current_only=True,limit=6,quality='GOOD')
            if not stage_pages:stage_pages=search(object_id,q,stage=stage,current_only=True,limit=6,quality='GOOD')
            if include_unselected and not stage_pages:
                stage_pages=search(object_id,p['config']['aliases'][0],stage=stage,limit=6,quality='GOOD')
                if not stage_pages:stage_pages=search(object_id,q,stage=stage,limit=6,quality='GOOD')
            pages.extend(stage_pages)
        pages=[page for page in pages if page['quality']=='GOOD' and (not file_ids or page['file_id'] in file_ids)]
        groups={s:[page for page in pages if page['stage']==s] for s in ('PD','RD','ID')}
        pages=[]
        while any(groups.values()):
            for group in groups.values():
                if group:pages.append(group.pop(0))
        for page in pages:
            if cancel and cancel():break
            if processed>=page_budget:break
            if include_unselected and processed-before_processed>=3:break
            doc=allowed[page['file_id']]
            lines=parameter_context(page['lines'],p)
            prompt=json.dumps({'task':'Извлеки ЗНАЧЕНИЕ, а не название указанного параметра. Для числового параметра value содержит только число из ячейки таблицы, например 123,4, без единиц и слов. Не возвращай подпись строки вместо числа. В таблице название и число расположены на одной высоте y, но в разных колонках x. Не путай итог с подстроками наземной/подземной части. Выбирай колонку по стадии документа. value — дословная подстрока строк; entity — номер конкретного помещения/элемента, дословно из тех же строк, либо OBJECT только для общих показателей здания. line_ids включают строку названия и строку значения. Если уверенного значения нет — items: [].','stage':doc['stage'],'parameter':{'code':p['code'],'name':p['name'],'unit':p['unit'],'scope':p['config']['scope']},'lines':[{'id':i,'text':l['text'],'x':round(l['bbox'][0],3),'y':round(l['bbox'][1],3)} for i,l in enumerate(lines)]},ensure_ascii=False)
            numeric_units={'м²','м³','мм','м','шт.','ед.','кВт','м³/сут','Гкал/ч','м³/ч','%','‰','мин','дни','чел.','тыс. руб.','Вт/(м·С)','м²·С/Вт','кВт·ч/м²'}
            reply=call_model(prompt,NumericExtractionReply if p['unit'] in numeric_units else ExtractionReply,'parameter-extraction')
            processed+=1
            for item in reply.items:
                if item.parameter_code!=p['code'] or any(i<0 or i>=len(lines) for i in item.line_ids):rejected+=1;continue
                # A compound parameter (flow / pressure / power) cannot be represented
                # by one scalar or an arbitrary snippet from a bill of materials.
                if p['unit']=='м³/ч / Па / кВт':rejected+=1;continue
                ls=[lines[i] for i in item.line_ids];quote=' '.join(l['text'] for l in ls)
                try:item.value=normalize_model_value(item.value,item.unit,p['unit'])
                except ValueError:rejected+=1;continue
                item.unit=p['unit']
                proposed=scalar(item.value,p['unit'])
                if not parameter_semantics_valid(p,quote,proposed):rejected+=1;continue
                if proposed['kind']=='number':
                    literals=[m.group(0) for m in re.finditer(NUMBER,quote)]
                    matches=[s for s in literals if scalar(s,p['unit'])['normalized']==proposed['normalized']]
                    if not matches:rejected+=1;continue
                    item.value=matches[0] # Preserve literal source spelling after decimal/space normalization.
                if norm(item.value).casefold() not in norm(quote).casefold():rejected+=1;continue
                if item.unit!=p['unit']:rejected+=1;continue
                if p['config']['scope']=='ELEMENT' and (item.entity=='OBJECT' or not re.search(r'\d',item.entity) or item.entity.casefold() not in quote.casefold()):rejected+=1;continue
                entity='OBJECT' if p['config']['scope']=='OBJECT' else 'ELEMENT:'+item.entity.upper()
                box=[min(l['bbox'][0] for l in ls),min(l['bbox'][1] for l in ls),max(l['bbox'][2] for l in ls),max(l['bbox'][3] for l in ls)]
                value=scalar(item.value,p['unit'])
                if value['kind']=='number':
                    source_unit=literal_unit(item.value,quote)
                    if source_unit and source_unit!=p['unit']:rejected+=1;continue
                    value['unit_basis']='SOURCE_LITERAL' if source_unit else 'PARAMETER_DEFAULT_REQUIRES_REVIEW'
                numeric_units={'м²','м³','мм','м','шт.','ед.','кВт','м³/сут','Гкал/ч','м³/ч','%','‰','мин','дни','чел.','тыс. руб.','Вт/(м·С)','м²·С/Вт','кВт·ч/м²'}
                if p['unit'] in numeric_units and value['kind']!='number':rejected+=1;continue
                if any(norm(item.value).casefold()==norm(a).casefold() for a in p['config']['aliases']):rejected+=1;continue
                ev={'file_id':doc['id'],'sha256':doc['sha256'],'stage':doc['stage'],'document_code':doc['document_code'],'revision':doc['revision'],'approval_status':doc['approval_status'],'page':page['page'],'bbox':box,'quote':quote,'value':value,'entity':entity,'coordinate_space':'visible_rotated_page_normalized'}
                added+=execute('INSERT INTO facts(id,file_id,object_id,parameter_code,entity,page,value,evidence,method,confidence) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO NOTHING',(uid(doc['id'],p['code'],entity,page['page'],box,item.value),doc['id'],object_id,p['code'],entity,page['page'],value,ev,TEXT_MODEL,.7))
            if callback:callback(processed,page_budget,p['code'])
        audit('parameter_extraction',object_id,{'code':p['code'],'retrieved_pages':len(pages),'model_pages':processed-before_processed,'facts_added':added-before_added,'unselected_sources_allowed':include_unselected,'scope':'LITERAL_EXTRACTION_ONLY'})
    return {'model_pages':processed,'facts_added':added,'rejected_ungrounded':rejected,'parameters_searched':len(attempted),'remaining_parameters':len(ps)-len(attempted),'cancelled':bool(cancel and cancel())}

def compare_values(expected,actual,tolerance=0):
    if expected['kind']!=actual['kind'] or expected['unit']!=actual['unit']:return None,None
    if expected['kind']=='number':
        delta=actual['normalized']-expected['normalized']
        relative=abs(delta)/abs(expected['normalized']) if expected['normalized'] else (0 if not delta else None)
        different=abs(delta)>1e-8 and (relative is None or relative>tolerance+1e-10)
        return different,{'absolute':delta,'relative':relative,'unit':expected['unit']}
    return expected['normalized']!=actual['normalized'],{'expected':expected['normalized'],'actual':actual['normalized']}

def comparison_state(parameter,expected,actual):
    different,delta=compare_values(expected,actual,parameter['config']['relative_tolerance'])
    if different is None:return 'NOT_COMPARABLE',delta
    if not different:return 'MATCH_PENDING_REVIEW',delta
    if parameter['config'].get('comparator')=='pd_decrease' and expected['kind']=='number' and actual['kind']=='number' and actual['normalized']>expected['normalized']:
        return 'NO_TRIGGER_PENDING_REVIEW',delta
    if parameter['config'].get('comparator')=='pd_increase' and expected['kind']=='number' and actual['kind']=='number' and actual['normalized']<expected['normalized']:
        return 'NO_TRIGGER_PENDING_REVIEW',delta
    return 'CANDIDATE',delta

def findings(object_id):
    rows=query('''SELECT f.*,COALESCE(r.status,f.machine_status) AS status,r.reason_code,r.comment,r.user_id,r.created_at AS reviewed_at,
    COALESCE(e.evidence,f.evidence) AS current_evidence,p.name AS parameter_name
    FROM findings f LEFT JOIN parameters p ON p.code=f.parameter_code
    LEFT JOIN LATERAL (SELECT * FROM reviews WHERE finding_id=f.id ORDER BY id DESC LIMIT 1) r ON true
    LEFT JOIN LATERAL (SELECT * FROM evidence_overrides WHERE finding_id=f.id ORDER BY id DESC LIMIT 1) e ON true
    WHERE f.object_id=%s AND f.active ORDER BY f.created_at DESC''',(object_id,))
    for f in rows:
        f['machine_expected_value']=f['expected_value'];f['machine_actual_value']=f['actual_value']
        for e in f['current_evidence']:
            if e.get('role')=='expected':f['expected_value']=e.get('value',f['expected_value'])
            if e.get('role')=='actual':f['actual_value']=e.get('value',f['actual_value'])
    return rows

def revalidate_facts(object_id):
    p=one("SELECT * FROM parameters WHERE ordinal=9")
    for f in query("SELECT f.* FROM facts f LEFT JOIN fact_validations v ON v.fact_id=f.id WHERE object_id=%s AND parameter_code=%s AND (v.fact_id IS NULL OR v.validator_version!='datum-relation-v2')",(object_id,p['code'])):
        accepted=parameter_semantics_valid(p,f['evidence']['quote'],f['value'])
        execute('''INSERT INTO fact_validations(fact_id,accepted,validator_version,reason) VALUES(%s,%s,%s,%s)
        ON CONFLICT(fact_id) DO UPDATE SET accepted=excluded.accepted,validator_version=excluded.validator_version,reason=excluded.reason,updated_at=now()''',(f['id'],accepted,'datum-relation-v2','Явная связь относительного нуля и абсолютного значения' if accepted else 'Значение не связано с нулевой отметкой здания; наличие числа и цитаты недостаточно'))
    for parameter in query('SELECT * FROM parameters WHERE ordinal IN (2,8,13)'):
        for f in query("SELECT f.* FROM facts f LEFT JOIN fact_validations v ON v.fact_id=f.id WHERE object_id=%s AND parameter_code=%s AND (v.fact_id IS NULL OR v.validator_version!='object-scope-v2')",(object_id,parameter['code'])):
            accepted=object_label_valid(parameter['ordinal'],f['evidence']['quote'])
            execute('''INSERT INTO fact_validations(fact_id,accepted,validator_version,reason) VALUES(%s,%s,%s,%s)
            ON CONFLICT(fact_id) DO UPDATE SET accepted=excluded.accepted,validator_version=excluded.validator_version,reason=excluded.reason,updated_at=now()''',(f['id'],accepted,'object-scope-v2','Область показателя: здание; номер корпуса, помещение и ёмкость оборудования не являются этим показателем'))

def compare(object_id,codes=None):
    from .coverage import SCALAR_RULES
    started=time.perf_counter()
    obj=one('SELECT * FROM objects WHERE id=%s',(object_id,))
    if obj['status']=='FINALIZED':raise ValueError('Проверка финализирована')
    selected,issues=revisions(object_id);byid={d['id']:d for d in selected}
    all_docs={d['id']:d for d in query("SELECT * FROM documents WHERE object_id=%s AND parse_status!='ERROR'",(object_id,))}
    revalidate_facts(object_id)
    allfacts=query("SELECT f.* FROM facts f JOIN pages pg ON pg.file_id=f.file_id AND pg.page=f.page AND pg.quality='GOOD' LEFT JOIN fact_validations v ON v.fact_id=f.id WHERE f.object_id=%s AND COALESCE(v.accepted,true) AND f.method NOT IN ('anchored','anchored-v2','anchored-v3') AND NOT(parameter_code='M-009' AND f.method='anchored-v4')",(object_id,))
    ps=[p for p in catalog() if codes is None or p['code'] in codes]
    created=0
    for p in ps:
        applicability=(obj['applicability'] or {}).get(p['code'])
        fs=[f for f in allfacts if f['parameter_code']==p['code'] and f['file_id'] in byid]
        missing=[];detail={'revision_issues':[],'facts':len(fs),'graphic_interpretation':p['config'].get('graphic_interpretation','NOT_IMPLEMENTED'),'automatic_absence_detection':'NOT_IMPLEMENTED','coverage_scope':'LITERAL_VALUE_COMPARISON'}
        full_scalar=p['ordinal'] in SCALAR_RULES
        impl=('IMPLEMENTED_TEXT' if p['config']['extractor']=='anchored' else 'IMPLEMENTED_ASSISTED') if full_scalar else 'PARTIAL_LITERAL_ONLY'
        status='MISSING_EVIDENCE';complete='MISSING_EVIDENCE';active_ids=[]
        if applicability and applicability.get('applicable') is False:
            status=complete='NOT_APPLICABLE';detail['reason']=applicability.get('reason')
        elif not fs:
            relevant={f['file_id'] for f in allfacts if f['parameter_code']==p['code']}
            related_issues=[i for i in issues if relevant.intersection(i['file_ids'])]
            if related_issues:status=complete='CLARIFICATION_REQUIRED';detail['revision_issues']=related_issues
            detail['reason']='Не найдены доказательные значения в выбранных редакциях'
            detail['execution']='MODEL_NOT_RUN_OR_NO_GROUNDED_VALUES' if p['config']['extractor']=='assisted' else 'NO_ANCHORED_VALUES'
        else:
            groups=collections.defaultdict(lambda:collections.defaultdict(list))
            for f in fs:groups[f['entity']][byid[f['file_id']]['stage']].append(f)
            outcomes=[]
            for entity,stages in groups.items():
                pd=stages.get('PD',[])
                for stage in ('RD','ID'):
                    actual=stages.get(stage,[])
                    if not actual:continue
                    if not pd:outcomes.append('MISSING_EVIDENCE');continue
                    # Multiple values in the same scope need disambiguation, never cross product.
                    uniq=lambda facts:{json.dumps([f['value']['kind'],f['value']['normalized'],f['value']['unit']],ensure_ascii=False) for f in facts}
                    if len(uniq(pd))>1 or len(uniq(actual))>1:outcomes.append('CLARIFICATION_REQUIRED');continue
                    a,b=pd[0],actual[0]
                    if byid[a['file_id']]['sha256']==byid[b['file_id']]['sha256']:
                        outcomes.append('NOT_COMPARABLE');detail['duplicate_across_stages']=True;continue
                    state,delta=comparison_state(p,a['value'],b['value'])
                    if state=='NOT_COMPARABLE':outcomes.append(state);continue
                    rationale='Сопоставление явно извлечённых значений ПД и '+stage+'. Согласованные изменения проверяет инспектор.'
                    if not full_scalar:
                        state='CLARIFICATION_REQUIRED'
                        rationale='Сопоставлены только текстовые значения. Полный метод этого параметра (состав, геометрия или внешняя проверка) не реализован; требуется содержательная проверка инспектора. Совпадение чисел не доказывает выполнение всего правила.'
                    ev=[]
                    for fact,role in ((a,'expected'),(b,'actual')):
                        source=byid[fact['file_id']]
                        ev.append(dict(fact['evidence'],role=role,document_code=source['document_code'],revision=source['revision'],approval_status=source['approval_status']))
                    fingerprint=uid('compare-v1' if full_scalar else 'compare-partial-v2',p['code'],entity,stage,a['id'],b['id'],p['config'])
                    if any((byid[f['file_id']].get('metadata') or {}).get('source_metadata_basis') for f in (a,b)):
                        fingerprint=uid(fingerprint,[(e['document_code'],e['revision'],e['approval_status']) for e in ev])
                    finding_id=uid(object_id,fingerprint);active_ids.append(finding_id)
                    execute('''INSERT INTO findings(id,object_id,parameter_code,rule_code,entity,machine_status,expected_value,actual_value,delta,evidence,rationale,priority,fingerprint,model_version)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET active=true''',(finding_id,object_id,p['code'],'PD_DIFF_'+p['code'],entity,state,a['value'],b['value'],delta,ev,rationale,p['priority'],fingerprint,'rules-v2/'+TEXT_MODEL))
                    outcomes.append(state);created+=1
            for candidate in ('CANDIDATE','CLARIFICATION_REQUIRED','NOT_COMPARABLE','MISSING_EVIDENCE','NO_TRIGGER_PENDING_REVIEW','MATCH_PENDING_REVIEW'):
                if candidate in outcomes:status=candidate;break
            complete='COMPLETE' if status in ('CANDIDATE','MATCH_PENDING_REVIEW','NO_TRIGGER_PENDING_REVIEW') else status
            detail['outcomes']=dict(collections.Counter(outcomes))
        # Show a grounded provisional pair even before the inspector selects revisions.
        # This is always CLARIFICATION_REQUIRED, never a candidate or a verified match.
        if not active_ids and status!='NOT_APPLICABLE':
            tentative=collections.defaultdict(lambda:collections.defaultdict(list))
            for fact in allfacts:
                if fact['parameter_code']==p['code'] and fact['file_id'] in all_docs:
                    tentative[fact['entity']][all_docs[fact['file_id']]['stage']].append(fact)
            for entity,stages in tentative.items():
                pd=stages.get('PD',[])
                for stage in ('RD','ID'):
                    actual=stages.get(stage,[])
                    unique=lambda rows:{json.dumps([f['value']['kind'],f['value']['normalized'],f['value']['unit']],sort_keys=True) for f in rows}
                    if len(unique(pd))!=1 or len(unique(actual))!=1:continue
                    a,b=pd[0],actual[0]
                    if a['file_id'] in byid and b['file_id'] in byid:continue
                    if all_docs[a['file_id']]['sha256']==all_docs[b['file_id']]['sha256']:continue
                    different,delta=compare_values(a['value'],b['value'],p['config']['relative_tolerance'])
                    if different is None:continue
                    fid=uid(object_id,'provisional-v1',p['code'],entity,stage,a['id'],b['id'])
                    ev=[dict(a['evidence'],role='expected'),dict(b['evidence'],role='actual')]
                    reason=('Предварительно найдены разные значения. ' if different else 'Предварительно значения совпали. ')+'Выберите актуальные редакции этих источников. До выбора это не результат завершённой сверки и не подтверждённое нарушение.'
                    execute('INSERT INTO findings(id,object_id,parameter_code,rule_code,entity,machine_status,expected_value,actual_value,delta,evidence,rationale,priority,fingerprint,model_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET active=true',(fid,object_id,p['code'],'PROVISIONAL_'+p['code'],entity,'CLARIFICATION_REQUIRED',a['value'],b['value'],delta,ev,reason,p['priority'],fid,'provisional-v1'))
                    active_ids.append(fid);created+=1;status=complete='CLARIFICATION_REQUIRED';detail['preliminary_sources']=True
        # Unchanged evidence keeps its ID and review; superseded findings stay in history.
        execute("UPDATE findings SET active=false WHERE object_id=%s AND parameter_code=%s AND model_version NOT IN ('inspector-manual-v1','bundle-train-v1','bundle-train-v2') AND NOT(id=ANY(%s::text[]))",(object_id,p['code'],'{'+','.join(active_ids)+'}'))
        execute('''INSERT INTO checks(object_id,parameter_code,implementation_status,completeness_status,finding_status,details) VALUES(%s,%s,%s,%s,%s,%s)
        ON CONFLICT(object_id,parameter_code) DO UPDATE SET implementation_status=excluded.implementation_status,completeness_status=excluded.completeness_status,finding_status=excluded.finding_status,details=excluded.details,updated_at=now()''',(object_id,p['code'],impl,complete,status,detail))
    execute("UPDATE objects SET status='READY' WHERE id=%s",(object_id,))
    result={'parameters_recomputed':len(ps),'evidence_groups':created,'seconds':round(time.perf_counter()-started,3),'revision_issues':len(issues)}
    audit('comparison',object_id,result)
    return result

def affected_parameters(file_ids):
    if not file_ids:return []
    ps=catalog();codes=set()
    for file_id in file_ids:
        doc=one('SELECT * FROM documents WHERE id=%s',(file_id,))
        codes.update(r['parameter_code'] for r in query('SELECT DISTINCT parameter_code FROM facts WHERE file_id=%s',(file_id,)))
        # Revision changes also invalidate earlier facts in the same logical document chain.
        codes.update(r['parameter_code'] for r in query('''SELECT DISTINCT f.parameter_code FROM facts f JOIN documents d ON d.id=f.file_id
        WHERE d.object_id=%s AND d.stage=%s AND d.document_code=%s''',(doc['object_id'],doc['stage'],doc['document_code'])))
        text=' '.join(r['text'] for r in query('SELECT text FROM pages WHERE file_id=%s',(file_id,))).lower()
        for p in ps:
            if any(alias in text for alias in p['config']['aliases']):codes.add(p['code'])
    return sorted(codes)

def free_search(object_id,q,entity=None):
    scope=uid(q.casefold().strip(),entity or '')
    pages=[]
    for stage in ('PD','RD','ID'):pages.extend(search(object_id,q,stage=stage,entity=entity,current_only=True,limit=3))
    refs=[]
    counts=collections.Counter()
    for p in pages:
        for l in context_lines(p['lines'],re.findall(r'[\w]+',q),budget=3500)[:60]:
            if counts[p['stage']]>=60:break
            refs.append(dict(l,file_id=p['file_id'],page=p['page'],stage=p['stage'],sha256=p['sha256'],document_code=p['document_code'],revision=p['revision'],approval_status=p['approval_status']))
            counts[p['stage']]+=1
    if not any(r['stage']=='PD' for r in refs) or not any(r['stage'] in ('RD','ID') for r in refs):
        execute("UPDATE findings SET active=false WHERE object_id=%s AND rule_code='FREE_SEARCH' AND search_scope=%s",(object_id,scope))
        return {'status':'MISSING_EVIDENCE','reason':'Нет сопоставимых актуальных ПД и РД/ИД','created':0}
    prompt=json.dumps({'task':'Найди гипотезы расхождений между ПД и РД/ИД за пределами заданной матрицы. ПД — эталон. Сравнивай только одно и то же помещение/элемент. Значения дословно из строк. Не проверяй нормативы. expected_ref и actual_ref — индексы.','query':q,'entity':entity,'fragments':[dict(id=i,stage=r['stage'],text=r['text']) for i,r in enumerate(refs)]},ensure_ascii=False)
    reply=call_model(prompt,HypothesisReply,'free-hypotheses');created=0;active_ids=[]
    for h in reply.hypotheses:
        if min(h.expected_ref,h.actual_ref)<0 or max(h.expected_ref,h.actual_ref)>=len(refs):continue
        a,b=refs[h.expected_ref],refs[h.actual_ref]
        if a['stage']!='PD' or b['stage'] not in ('RD','ID') or a['sha256']==b['sha256']:continue
        if h.expected_value.casefold() not in a['text'].casefold() or h.actual_value.casefold() not in b['text'].casefold():continue
        if h.entity!='OBJECT' and (h.entity.casefold() not in a['text'].casefold() or h.entity.casefold() not in b['text'].casefold()):continue
        if compare_values(scalar(h.expected_value,''),scalar(h.actual_value,''))[0] is not True:continue
        ev=[dict(a,quote=a['text'],role='expected',value=scalar(h.expected_value,'')),dict(b,quote=b['text'],role='actual',value=scalar(h.actual_value,''))]
        fid=uid(object_id,'hypothesis-v2',a,b,h.entity,h.expected_value,h.actual_value,scope);active_ids.append(fid)
        created+=execute('INSERT INTO findings(id,object_id,rule_code,entity,machine_status,expected_value,actual_value,evidence,rationale,priority,fingerprint,model_version,search_scope) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET active=true',(fid,object_id,'FREE_SEARCH',h.entity,'SUSPICION',scalar(h.expected_value,''),scalar(h.actual_value,''),ev,h.description,'MEDIUM',fid,TEXT_MODEL,scope))
    execute("UPDATE findings SET active=false WHERE object_id=%s AND rule_code='FREE_SEARCH' AND search_scope=%s AND NOT(id=ANY(%s::text[]))",(object_id,scope,'{'+','.join(active_ids)+'}'))
    return {'status':'DONE','created':created,'model_hypotheses':len(reply.hypotheses)}
