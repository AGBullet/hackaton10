"""Explicit GOLD gate. Local role decisions alone are not expert-approved training data."""
import os,uuid
from .db import one,execute
from .protocols import prepare_dataset

def readiness():
    from pathlib import Path
    dataset=prepare_dataset()
    labels=dataset['labels'];missing=[]
    for key,title in [('CONFIRMED_VIOLATION','подтверждённых расхождений'),('NEGATIVE_VERIFIED','проверенных отрицательных случаев')]:
        if labels.get(key,0)<30:missing.append(f"Нужно ещё {30-labels.get(key,0)} {title} с экспертной проверкой и разрешением на обучение")
    if dataset['object_groups']<5:missing.append(f"Нужно ещё {5-dataset['object_groups']} независимых групп объектов для разделения train / validation / test")
    if not os.getenv('TRAINING_PYTHON') or not os.getenv('TRAINING_MODEL_PATH'):missing.append('Не настроен локальный обучающий Python и исходная модель Transformers; GGUF в LM Studio предназначен для инференса')
    elif not Path(os.environ['TRAINING_PYTHON']).is_file() or not Path(os.environ['TRAINING_MODEL_PATH']).is_dir():missing.append('Не найдены настроенный Python или каталог исходной модели Transformers')
    return {'training_allowed':not missing,'gold_ready':dataset['training_allowed'],'expert_verified_records':dataset['records'],'missing':missing,'dataset':dataset,'publication_allowed':False}

def start():
    result=readiness()
    if not result['training_allowed']:return {'started':False,**result}
    from .jobs import enqueue
    from .db import connection
    with connection() as guard:
        guard.execute('SELECT pg_advisory_xact_lock(89132354)')
        existing=one("SELECT * FROM training_runs WHERE status IN ('QUEUED','RUNNING') ORDER BY created_at DESC LIMIT 1")
        if existing:return {'started':True,'run_id':existing['id'],'job_id':existing['job_id'],'deduplicated':True}
        run_id=str(uuid.uuid4())
        execute('INSERT INTO training_runs(id,dataset_id,status,details) VALUES(%s,%s,%s,%s)',(run_id,result['dataset']['sha256'],'QUEUED',result))
        job=enqueue('training',payload={'run_id':run_id})
        execute('UPDATE training_runs SET job_id=%s WHERE id=%s',(job['id'],run_id))
        return {'started':True,'run_id':run_id,'job_id':job['id']}

def run_training(run_id):
    import subprocess
    from .config import ROOT,DATA
    run=one('SELECT * FROM training_runs WHERE id=%s',(run_id,))
    if not run:raise ValueError('Запуск обучения не найден')
    if run['status']=='DONE':return run['details']
    current=readiness()
    if not current['training_allowed'] or current['dataset']['sha256']!=run['dataset_id']:
        execute("UPDATE training_runs SET status='ERROR',updated_at=now() WHERE id=%s",(run_id,))
        raise ValueError('Состав или готовность GOLD изменились. Проверьте выборку заново.')
    execute("UPDATE training_runs SET status='RUNNING',updated_at=now() WHERE id=%s",(run_id,))
    log=DATA/'logs'/f'training-{run_id}.log'
    try:
        with log.open('w',encoding='utf-8') as out:
            # Share the same cross-process GPU lock as LM inference.
            from .db import connection
            with connection() as c:
                c.execute('SELECT pg_advisory_lock(89132351)')
                try:
                    import shutil
                    from .config import TEXT_MODEL,VISION_MODEL
                    lms=shutil.which('lms')
                    if lms:
                        listing=subprocess.run([lms,'ps'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15,check=True).stdout
                        for model in (TEXT_MODEL,VISION_MODEL):
                            if model in listing:subprocess.run([lms,'unload',model],capture_output=True,timeout=60,check=True)
                    subprocess.run([os.environ['TRAINING_PYTHON'],str(ROOT/'scripts/train_adapter.py'),'--dataset',str(ROOT/current['dataset']['path']),'--model',os.environ['TRAINING_MODEL_PATH'],'--output',str(DATA/'adapters'/run_id)],cwd=ROOT,stdout=out,stderr=subprocess.STDOUT,check=True,timeout=21600,env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
                finally:c.execute('SELECT pg_advisory_unlock(89132351)')
        result={'adapter_path':f'data/adapters/{run_id}','publication_allowed':False,'status':'AWAITING_INDEPENDENT_EVALUATION'}
        execute("UPDATE training_runs SET status='DONE',details=%s,updated_at=now() WHERE id=%s",(result,run_id));return result
    except Exception:
        execute("UPDATE training_runs SET status='ERROR',updated_at=now() WHERE id=%s",(run_id,));raise
