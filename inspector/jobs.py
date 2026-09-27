import uuid,hashlib,json
from .db import one,execute,connection,params

def enqueue(kind,object_id=None,payload=None):
    payload=payload or {}
    request_key=hashlib.sha256(json.dumps([kind,object_id,payload],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(89132352)')
        same=c.execute("SELECT id,status FROM jobs WHERE request_key=%s AND status IN ('QUEUED','RUNNING')",(request_key,)).fetchone()
        if same:return {**same,'deduplicated':True}
        if object_id:
            obj=c.execute('SELECT status FROM objects WHERE id=%s FOR UPDATE',(object_id,)).fetchone()
            if not obj:raise ValueError('Объект не найден')
            if obj['status']=='FINALIZED':raise ValueError('Протокол финализирован')
            running=c.execute("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING')",(object_id,)).fetchone()
            if running:raise ValueError('Для объекта уже выполняется задача; дождитесь завершения или приостановите её')
        id_=str(uuid.uuid4())
        c.execute('INSERT INTO jobs(id,object_id,kind,payload,request_key) VALUES(%s,%s,%s,%s,%s)',params((id_,object_id,kind,payload,request_key)))
    return {'id':id_,'status':'QUEUED'}

def progress(id_,done,total,message):execute('UPDATE jobs SET progress=%s,total=%s,message=%s,updated_at=now() WHERE id=%s',(done,total,message,id_))
def cancelled(id_):return bool(one('SELECT cancel_requested FROM jobs WHERE id=%s',(id_,))['cancel_requested'])

def checkpoint(id_,state):execute('UPDATE jobs SET checkpoint=%s,updated_at=now() WHERE id=%s',(state,id_))

def pause(id_):
    with connection() as c:
        job=c.execute('SELECT status FROM jobs WHERE id=%s FOR UPDATE',(id_,)).fetchone()
        if not job:raise ValueError('Задача не найдена')
        if job['status'] not in ('QUEUED','RUNNING','PAUSED'):raise ValueError('Задача уже завершена')
        c.execute("UPDATE jobs SET cancel_requested=true,status=CASE WHEN status='QUEUED' THEN 'PAUSED' ELSE status END,message=CASE WHEN status='QUEUED' THEN 'Приостановлено до начала обработки' ELSE message END,updated_at=now() WHERE id=%s",(id_,))
    return {'ok':True}

def resume(id_):
    with connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(89132352)')
        job=c.execute('SELECT * FROM jobs WHERE id=%s FOR UPDATE',(id_,)).fetchone()
        if not job:raise ValueError('Задача не найдена')
        if job['status'] not in ('PAUSED','ERROR','DONE_WITH_ERRORS'):raise ValueError('Задача ещё выполняется или уже завершена')
        if job['object_id']:
            obj=c.execute('SELECT status FROM objects WHERE id=%s FOR UPDATE',(job['object_id'],)).fetchone()
            if obj['status']=='FINALIZED':raise ValueError('Протокол финализирован')
            if c.execute("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING')",(job['object_id'],)).fetchone():raise ValueError('Дождитесь завершения текущей задачи объекта')
        state=job.get('checkpoint') or {}
        state['completed']={k:v for k,v in state.get('completed',{}).items() if not v.get('error')}
        if (state.get('model') or {}).get('status')=='ERROR':state.pop('model_done',None)
        state['resume_generation']=state.get('resume_generation',0)+1
        c.execute("UPDATE jobs SET status='QUEUED',cancel_requested=false,message='Продолжение с кеша',attempts=0,checkpoint=%s,available_at=now(),updated_at=now() WHERE id=%s",params((state,id_)))
    return {'ok':True}

def claim_next():
    with connection() as c:
        job=c.execute("SELECT id FROM jobs WHERE status='QUEUED' AND available_at<=now() ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
        if not job:return None
        return c.execute("UPDATE jobs SET status='RUNNING',attempts=attempts+1,updated_at=now() WHERE id=%s RETURNING *",(job['id'],)).fetchone()

def recover_interrupted():
    # Called only by the worker holding its lifetime advisory lock.
    return execute("UPDATE jobs SET status=CASE WHEN cancel_requested THEN 'PAUSED' ELSE 'QUEUED' END,message='Продолжение после перезапуска',available_at=now(),updated_at=now() WHERE status='RUNNING'")

def transient_error(error):
    import httpx
    return isinstance(error,(httpx.TimeoutException,httpx.ConnectError,TimeoutError)) or (isinstance(error,httpx.HTTPStatusError) and error.response.status_code in (408,429,502,503,504))

def record_failure(job,error):
    retry=transient_error(error) and job['attempts']<3 and not cancelled(job['id'])
    state='QUEUED' if retry else 'PAUSED' if cancelled(job['id']) else 'ERROR'
    message=f"Временная ошибка. Повтор {job['attempts']+1} из 3" if retry else 'Обработка остановлена из-за ошибки. Можно повторить.'
    execute("UPDATE jobs SET status=%s,message=%s,result=%s,available_at=now()+(%s * interval '1 second'),updated_at=now() WHERE id=%s",(state,message,{'error':str(error),'error_type':type(error).__name__},2**job['attempts'] if retry else 0,job['id']))
    return state
