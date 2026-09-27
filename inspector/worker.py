import json,os,time,traceback,sys
import psycopg
from .config import PG,DATA
from .db import query,one,execute,connection,audit
from .jobs import progress,cancelled,checkpoint,claim_next,recover_interrupted,record_failure,transient_error
from .registry import ingest_corpus
from .catalog import catalog
from .extraction import parse_document,extract_facts,document_pdf
from .comparison import compare,affected_parameters,assisted_extract,free_search
from .protocols import snapshot,prepare_dataset

def parse_source(d,p,plan,job_id,schema):
    """CPU-only child process: PyMuPDF is never shared between threads."""
    os.environ.setdefault('OMP_THREAD_LIMIT','1')
    from . import db
    db.SCHEMA=schema
    started=time.perf_counter()
    try:
        result=parse_document(d['id'],p.get('max_pages',0),p.get('ocr',True),
            cancel=lambda:cancelled(job_id),page_numbers=plan)
        entry={'file_id':d['id'],**result,'parameters':extract_facts(d['id'],catalog())}
        if any(r['quality']=='ERROR' for r in result['quality']):entry['error']='Часть страниц не удалось извлечь'
    except Exception as error:
        if transient_error(error):raise
        entry={'file_id':d['id'],'error':str(error)}
        audit('parse_error',d['object_id'],entry)
    entry['wall_seconds']=round(time.perf_counter()-started,3)
    return entry

def bulk_sources(docs,p,state,job_id):
    from concurrent.futures import ProcessPoolExecutor,wait,FIRST_COMPLETED
    import multiprocessing
    from .db import SCHEMA
    import psutil
    requested=min(4,max(1,int(os.getenv('OCR_WORKERS','2'))))
    workers=min(requested,max(1,int(psutil.virtual_memory().available/(1.25*1024**3))))
    remaining=iter(d for d in docs if d['id'] not in state.get('completed',{}))
    with ProcessPoolExecutor(max_workers=workers,mp_context=multiprocessing.get_context('spawn')) as pool:
        pending={}
        def fill():
            while len(pending)<workers and not cancelled(job_id):
                d=next(remaining,None)
                if d is None:break
                pending[pool.submit(parse_source,d,p,state['plan'][d['id']],job_id,SCHEMA)]=d
        fill()
        while pending:
            ready,_=wait(pending,timeout=1,return_when=FIRST_COMPLETED)
            for future in ready:
                d=pending.pop(future)
                yield d,future.result()
            fill()

def process(job):
    id_=job['id'];obj=job['object_id'];p=job['payload'];kind=job['kind']
    snapshot_key=id_+':'+str((job.get('checkpoint') or {}).get('resume_generation',0))
    if kind=='scan':return {'registered':ingest_corpus(p.get('limit',0),lambda n:progress(id_,n,0,f'Добавлено в реестр: {n} файлов'))}
    if kind=='dataset':return prepare_dataset()
    if kind=='training':
        from .training import run_training
        return run_training(p['run_id'])
    if kind in ('parse','workflow'):
        docs=query("SELECT * FROM documents WHERE object_id=%s AND parse_status!='UNSUPPORTED' ORDER BY size_bytes",(obj,))
        ids=p.get('file_ids')
        if ids:docs=[d for d in docs if d['id'] in ids]
        if p.get('all_documents'):
            # Surface useful PD/RD pairs early while still processing the entire package.
            first=[]
            for stage in ('PD','RD','ID'):
                group=[d for d in docs if d['stage']==stage and d['format']=='.pdf']
                group.sort(key=lambda d:(d['discipline'] not in ('ПЗ','АР','КЖ'),d['size_bytes']))
                first.extend(group[:3])
            first.extend(d for d in docs if d['stage']=='PD' and d['name'].startswith('ПЗЭ') and d not in first)
            early={d['id'] for d in first};docs=first+[d for d in docs if d['id'] not in early]
        if p.get('file_limit') and not ids:
            groups={stage:[d for d in docs if d['stage']==stage] for stage in ('PD','RD','ID','UNKNOWN')}
            for group in groups.values():group.sort(key=lambda d:(d['format']!='.pdf',d['discipline'] not in ('ПЗ','АР','КЖ'),d['parse_status']=='PARSED',d['size_bytes']))
            balanced=[]
            while any(groups.values()) and len(balanced)<p['file_limit']:
                for group in groups.values():
                    if group and len(balanced)<p['file_limit']:balanced.append(group.pop(0))
            docs=balanced
        import fitz
        state=job.get('checkpoint') or {}
        if 'plan' not in state:
            plan={}
            for plan_index,d in enumerate(docs):
                if plan_index%25==0:progress(id_,plan_index,len(docs),'Проверка состава и страниц комплекта')
                try:
                    with fitz.open(document_pdf(d)) as pdf:total=len(pdf)
                    existing={r['page']:r for r in query('SELECT page,quality,parser_version FROM pages WHERE file_id=%s',(d['id'],))}
                    from .config import PARSER_VERSION
                    pending=[n for n in range(1,total+1) if n not in existing or existing[n]['parser_version']!=PARSER_VERSION or existing[n]['quality']=='ERROR' or (existing[n]['quality']=='OCR_REQUIRED' and p.get('ocr',True))]
                    plan[d['id']]=pending[:p['max_pages']] if p.get('max_pages') else pending
                except Exception as error:
                    if transient_error(error):raise
                    # A damaged source is a document error, not a reason to lose the package.
                    plan[d['id']]=None
            state.update(plan=plan,completed={})
            checkpoint(id_,state)
        docs=[d for d in docs if d['id'] in state['plan']]
        active_ids={d['id'] for d in docs}
        state['skipped']={fid:entry for fid,entry in state.get('completed',{}).items() if fid not in active_ids}
        state['completed']={fid:entry for fid,entry in state.get('completed',{}).items() if fid in active_ids}
        results=list(state.get('completed',{}).values());changed=list(state.get('completed',{}));ps=catalog()
        if p.get('all_documents'):
            for d,entry in bulk_sources(docs,p,state,id_):
                results.append(entry);changed.append(d['id'])
                if not cancelled(id_):state['completed'][d['id']]=entry
                checkpoint(id_,state)
                done=len(state['completed'])
                progress(id_,done,len(docs),f'Обработано файлов: {done}/{len(docs)} · чтение и OCR')
                if done%25==0 and not cancelled(id_):compare(obj)
        for i,d in enumerate([] if p.get('all_documents') else docs):
            if cancelled(id_):break
            if d['id'] in state.get('completed',{}):continue
            try:
                def cb(n,total):progress(id_,i,len(docs),f"{d['name']} · страница {n}/{total}")
                result=parse_document(d['id'],p.get('max_pages',0),p.get('ocr',True),cb,lambda:cancelled(id_),page_numbers=state['plan'][d['id']])
                codes=extract_facts(d['id'],ps);changed.append(d['id']);entry={'file_id':d['id'],**result,'parameters':codes}
                if any(r['quality']=='ERROR' for r in result['quality']):entry['error']='Часть страниц не удалось извлечь'
                # Results become visible after each document, not only after the entire package.
                # Large packages publish in batches; avoid rereading the entire object for every small act.
                if not p.get('all_documents') or (i+1)%25==0 or i==len(docs)-1:
                    existing=one('SELECT count(*) AS n FROM checks WHERE object_id=%s',(obj,))['n']
                    recent=changed[-25:] if p.get('all_documents') else [d['id']]
                    compare(obj,affected_parameters(recent) if existing==132 else None)
            except Exception as e:
                if transient_error(e):raise
                entry={'file_id':d['id'],'error':str(e)};audit('parse_error',obj,entry)
                compare(obj,affected_parameters([d['id']]))
            results.append(entry)
            if not cancelled(id_):
                state['completed'][d['id']]=entry
                checkpoint(id_,state)
            progress(id_,i+1,len(docs),f"Готово документов: {i+1}/{len(docs)}")
        model_result=state.get('model')
        if kind=='workflow' and p.get('use_model') and not cancelled(id_) and not state.get('model_done'):
            codes=p.get('codes') or (None if p.get('all_documents') else affected_parameters(changed)[:8] or ['M-001','M-009','M-055'])
            try:
                model_result=assisted_extract(obj,codes,p.get('page_budget',6),lambda n,t,c:progress(id_,n,t,'Проверка извлечённых значений моделью: '+c),file_ids=ids,include_unselected=p.get('all_documents',False),cancel=lambda:cancelled(id_))
                compare(obj,codes)
            except Exception as e:
                if transient_error(e):raise
                model_result={'status':'ERROR','reason':str(e),'text_results_preserved':True};audit('model_warning',obj,model_result)
            state.update(model=model_result,model_done=not (model_result or {}).get('cancelled',False));checkpoint(id_,state)
        if not cancelled(id_):
            if p.get('all_documents'):compare(obj)
            snapshot(obj,user='worker',job_id=snapshot_key)
        return {'documents':results,'affected_parameters':affected_parameters(changed),'model':model_result}
    if kind=='compare':
        result=compare(obj,p.get('codes'));snapshot(obj,user='worker',job_id=snapshot_key);return result
    if kind=='assisted':
        result=assisted_extract(obj,p.get('codes'),p.get('page_budget',12),lambda n,t,c:progress(id_,n,t,'Извлечение '+c),cancel=lambda:cancelled(id_))
        compare(obj,p.get('codes'));snapshot(obj,user='worker',job_id=snapshot_key);return result
    if kind=='free_search':
        result=free_search(obj,p.get('query',''),p.get('entity'));snapshot(obj,user='worker',job_id=snapshot_key);return result
    raise ValueError('Неизвестная задача')

def main():
    owner=psycopg.connect(**PG,autocommit=True)
    if not owner.execute('SELECT pg_try_advisory_lock(89132350)').fetchone()[0]:
        print('Worker is already running',flush=True);return
    recover_interrupted()
    print('Inspector worker ready; one job / one heavy request at a time',flush=True)
    try:
        while True:
            job=claim_next()
            if not job:time.sleep(1);continue
            try:
                result=process(job)
                errors=any(d.get('error') for d in result.get('documents',[])) or (result.get('model') or {}).get('status')=='ERROR'
                status='PAUSED' if cancelled(job['id']) else 'DONE_WITH_ERRORS' if errors else 'DONE'
                message={'PAUSED':'Приостановлено; прогресс сохранён','DONE_WITH_ERRORS':'Обработка завершена частично. Некоторые источники требуют повторной обработки.','DONE':'Обработка завершена'}[status]
                execute('UPDATE jobs SET status=%s,result=%s,message=%s,updated_at=now() WHERE id=%s',(status,result,message,job['id']))
            except Exception as e:
                traceback.print_exc()
                status=record_failure(job,e)
            print(json.dumps({'job':job['id'],'kind':job['kind'],'status':status},ensure_ascii=False),flush=True)
    except KeyboardInterrupt:pass
    finally:owner.close()

if __name__=='__main__':main()
