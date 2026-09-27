import asyncio,hashlib,io,json,math,os,re,shutil,sqlite3,threading,time,uuid,datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
import fitz
import psutil
import httpx
from fastapi import FastAPI,HTTPException,Request,UploadFile,File,Form,Query
from fastapi.responses import FileResponse,JSONResponse,Response,StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field,ConfigDict
from .config import ROOT,DATA,PG,VISION_MODEL
from .db import init,query,one,execute,audit,connection,params
from .registry import create_object,register,SUPPORTED,uid,inside_root
from .catalog import load_catalog,catalog
from .extraction import document_pdf,scalar,render_region,pdf_lines,ocr_image
from .comparison import revisions,findings,search,affected_parameters
from .jobs import enqueue
from .protocols import completeness,build_snapshot,snapshot,review,validate_evidence,export_pdf,prepare_dataset
from .models import available,call_model,VisionReply,last_execution
from .access import authorize,principal,login,current_user,initialize_credentials
from .workflow import readiness,extracted_values,prepare_job,manual_result,quality_report
from .navigation import documents_with_findings,document_findings
from .bundles import migrate_train_bbox_coordinates

PDF_LOCK=threading.RLock()

@asynccontextmanager
async def lifespan(app):
    init()
    initialize_credentials()
    if one('SELECT count(*) AS n FROM parameters')['n']!=132:load_catalog()
    migrate_train_bbox_coordinates()
    yield

app=FastAPI(title='Инспектор ИИ',version='0.1.0',lifespan=lifespan)

@app.exception_handler(ValueError)
async def value_error(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)

@app.exception_handler(httpx.HTTPError)
async def model_connection_error(request,exc):
    audit('local_api_error',details={'path':request.url.path,'error':str(exc)})
    return JSONResponse({'detail':'Не удалось получить ответ локальной модели. Проверьте сервер LM Studio и повторите чтение. Документы и результаты сохранены.'},status_code=503)

@app.middleware('http')
async def local_boundary(request,call_next):
    # Local by default. ALLOW_REMOTE=1 opens Host/Origin for temporary public tunnels (ngrok/cloudflared).
    allow_remote=os.getenv('ALLOW_REMOTE','0').strip() in ('1','true','yes','on')
    host=request.headers.get('host','').split(':')[0].lower()
    local_hosts={'127.0.0.1','localhost','testserver'}
    if not allow_remote and host not in local_hosts:
        return JSONResponse({'detail':'Сервис доступен только локально'},status_code=403)
    if request.method in ('POST','PUT','PATCH','DELETE'):
        origin=request.headers.get('origin')
        allowed_origins={'http://127.0.0.1:8000','http://localhost:8000'}
        if host in local_hosts:allowed_origins.add(str(request.base_url).rstrip('/'))
        if allow_remote and origin:
            # Accept same-host browser Origin from the temporary public URL.
            from urllib.parse import urlparse
            parsed=urlparse(origin)
            if parsed.scheme in ('http','https') and parsed.hostname:
                allowed_origins.add(origin.rstrip('/'))
        if origin and origin.rstrip('/') not in allowed_origins and not allow_remote:
            return JSONResponse({'detail':'Недопустимый источник запроса'},status_code=403)
        if origin and allow_remote:
            from urllib.parse import urlparse
            parsed=urlparse(origin)
            if parsed.scheme not in ('http','https') or not parsed.hostname:
                return JSONResponse({'detail':'Недопустимый источник запроса'},status_code=403)
        length=request.headers.get('content-length')
        if length and int(length)>202*1024**2:return JSONResponse({'detail':'Пакет превышает 200 МиБ'},status_code=413)
    try:identity=authorize(request)
    except HTTPException as e:return JSONResponse({'detail':e.detail},status_code=e.status_code)
    token=current_user.set(identity['user_id'])
    try:
        start=time.perf_counter();response=await call_next(request)
    finally:current_user.reset(token)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='SAMEORIGIN'
    response.headers['Referrer-Policy']='same-origin'
    response.headers['Server-Timing']=f'app;dur={(time.perf_counter()-start)*1000:.1f}'
    return response

class Body(BaseModel):model_config=ConfigDict(extra='forbid')
class SessionBody(Body):role:Literal['inspector','admin','ml_engineer'];password:str=''
class WorkingSetBody(Body):file_ids:list[str]=Field(min_length=2,max_length=12);reason:str=Field(min_length=3);max_pages:int=Field(default=25,ge=0,le=10000);use_model:bool=True
class ManualEvidence(Body):
    file_id:str
    page:int=Field(ge=1)
    bbox:list[float]=Field(min_length=4,max_length=4)
    role:Literal['expected','actual']
    raw:str=Field(min_length=1,max_length=200)
    quote:str=Field(min_length=1,max_length=10000)
class ManualBody(Body):parameter_code:str;entity:str='OBJECT';evidence:list[ManualEvidence]=Field(min_length=2,max_length=2);reason:str=Field(min_length=3)
class ObjectBody(Body):name:str=Field(min_length=1,max_length=200);address:str=''
class JobBody(Body):
    kind:Literal['parse','compare','assisted','free_search','workflow']
    use_model:bool=True
    all_documents:bool=False
    file_ids:list[str]=[]
    file_limit:int=Field(default=0,ge=0,le=10000)
    max_pages:int=Field(default=0,ge=0,le=10000)
    ocr:bool=True
    codes:list[str]|None=None
    page_budget:int=Field(default=12,ge=1,le=1000)
    query:str=''
    entity:str|None=None
class ReviewBody(Body):
    status:str
    reason_code:str
    comment:str=Field(min_length=1)
    user_id:str=Field(default='inspector-local',min_length=1)
    expert_verified:bool=False
    training_consent:bool=False
class ChoiceBody(Body):file_id:str;reason:str=Field(min_length=3);user_id:str='inspector-local'
class EvidenceBody(Body):evidence:list[dict];reason:str=Field(min_length=3);user_id:str='inspector-local'
class CorrectionBody(Body):evidence:list[ManualEvidence]=Field(min_length=2,max_length=10);reason:str=Field(min_length=3)
class ManifestBody(Body):documents:list[dict];reason:str=Field(min_length=3);user_id:str='inspector-local'
class ApplicabilityBody(Body):applicable:bool;reason:str=Field(min_length=3);user_id:str='inspector-local'
class CropBody(Body):page:int=Field(ge=1);bbox:list[float]=Field(min_length=4,max_length=4);prompt:str='Прочитай подписи и размеры. Отметь неразборчивое. Не делай вывод о нарушении.'
class MeasureBody(Body):
    page:int=Field(ge=1)
    reference_points:list[float]=Field(min_length=4,max_length=4)
    reference_mm:float=Field(gt=0)
    measure_points:list[float]=Field(min_length=4,max_length=4)

def get_object(id_):
    obj=one('SELECT * FROM objects WHERE id=%s',(id_,))
    if not obj:raise HTTPException(404,'Объект не найден')
    return obj
def get_doc(id_):
    doc=one('SELECT * FROM documents WHERE id=%s',(id_,))
    if not doc:raise HTTPException(404,'Файл не найден')
    return doc
def ensure_editable(id_):
    obj=get_object(id_)
    if obj['status']=='FINALIZED':raise HTTPException(409,'Протокол финализирован; создайте новую проверку')
    if one("SELECT id FROM jobs WHERE object_id=%s AND status IN ('QUEUED','RUNNING') LIMIT 1",(id_,)):
        raise HTTPException(409,'Дождитесь завершения или приостановите текущую обработку объекта')
    return obj

@app.get('/api/health')
def health():
    return {'status':'ok','database':one('SELECT current_database() AS name,current_schema() AS schema'),'disk_free_gib':round(shutil.disk_usage(ROOT).free/1024**3,1),'ram_available_gib':round(psutil.virtual_memory().available/1024**3,2),'heavy_concurrency':1,'worker_running':bool(one('SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype=\'advisory\' AND objid=89132350) AS yes')['yes'])}
@app.get('/api/session')
def session(request:Request):return principal(request)
@app.post('/api/session')
def change_session(request:Request,body:SessionBody):
    token=login(body.role,body.password,request.client.host if request.client else 'local')
    response=JSONResponse({'ok':True})
    response.set_cookie('inspector_session',token,httponly=True,samesite='strict',max_age=28800)
    return response
@app.get('/api/quality')
def quality():return quality_report()
@app.get('/api/models')
def models():return available()
@app.get('/api/objects')
def objects():
    return query('''SELECT o.*, (SELECT count(*) FROM documents d WHERE d.object_id=o.id) AS files,
    (SELECT count(*) FROM findings f WHERE f.object_id=o.id AND f.active) AS groups,
    (SELECT jsonb_object_agg(stage,n) FROM (SELECT stage,count(*) AS n FROM documents WHERE object_id=o.id GROUP BY stage) s) AS stage_counts,
    (SELECT count(*) FROM documents d WHERE d.object_id=o.id AND d.parsed_pages>0) AS processed_files FROM objects o ORDER BY o.name''')
@app.post('/api/objects')
def new_object(body:ObjectBody):return get_object(create_object(body.name,address=body.address))
@app.get('/api/objects/{id_}')
def object_detail(id_):
    obj=get_object(id_);selected,issues=revisions(id_)
    return {'object':obj,'completeness':completeness(id_),'selected_file_ids':[d['id'] for d in selected],'revision_issues':issues,'summary':build_snapshot(id_)['summary'],'readiness':readiness(id_)}
@app.get('/api/objects/{id_}/coverage')
def object_coverage_report(id_):
    get_object(id_)
    from .coverage import object_coverage
    return object_coverage(id_)

@app.get('/api/objects/{id_}/extractions')
def extractions(id_):get_object(id_);return extracted_values(id_)
@app.post('/api/objects/{id_}/prepare')
def prepare(id_,body:WorkingSetBody):
    ensure_editable(id_)
    return prepare_job(id_,body.file_ids,body.reason,current_user.get(),body.max_pages,body.use_model)
@app.post('/api/objects/{id_}/findings/manual')
def manual(id_,body:ManualBody):
    ensure_editable(id_)
    return manual_result(id_,body.parameter_code,body.entity,[e.model_dump() for e in body.evidence],body.reason,current_user.get())
@app.get('/api/objects/{id_}/documents')
def documents(id_,stage:str|None=None,category:str|None=None,sort:str='name',offset:int=Query(0,ge=0),limit:int=Query(500,ge=1,le=10000)):
    get_object(id_)
    return documents_with_findings(id_,stage,category,sort,offset,limit)

@app.get('/api/documents/{id_}/findings')
def file_findings(id_,category:str|None=None):
    d=get_doc(id_)
    return document_findings(d['object_id'],id_,category)
@app.post('/api/scan')
def scan(limit:int=Query(0,ge=0)):return enqueue('scan',payload={'limit':limit})
@app.get('/api/archives')
def archives():
    journal=ROOT/'_extracted/extraction.sqlite'
    if not journal.exists():return []
    with sqlite3.connect(journal,timeout=5) as c:
        c.row_factory=sqlite3.Row
        return [dict(r) for r in c.execute('SELECT a.*, (SELECT count(*) FROM members m WHERE m.archive=a.key) AS files FROM archives a ORDER BY path')]
@app.post('/api/objects/{id_}/jobs')
def new_job(id_,body:JobBody):
    get_object(id_)
    for f in body.file_ids:
        if get_doc(f)['object_id']!=id_:raise ValueError('Файл принадлежит другому объекту')
    if body.codes and not set(body.codes).issubset({p['code'] for p in catalog()}):raise ValueError('Неизвестный код параметра')
    return enqueue(body.kind,id_,body.model_dump(exclude={'kind'}))
@app.get('/api/jobs')
def jobs(object_id:str|None=None):return query('SELECT * FROM jobs'+(' WHERE object_id=%s' if object_id else '')+' ORDER BY created_at DESC LIMIT 40',(object_id,) if object_id else ())
@app.post('/api/jobs/{id_}/pause')
def pause_job(id_):
    from .jobs import pause
    return pause(id_)
@app.post('/api/jobs/{id_}/resume')
def resume_job(id_):
    from .jobs import resume
    return resume(id_)
@app.get('/api/events')
async def events(request:Request):
    async def stream():
        while not await request.is_disconnected():
            rows=await asyncio.to_thread(jobs,None)
            yield 'data: '+json.dumps(rows,ensure_ascii=False,default=str)+'\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(stream(),media_type='text/event-stream')

@app.post('/api/objects/{id_}/upload')
async def upload(id_,files:list[UploadFile]=File(...),stage:str=Form(...),discipline:str=Form('UNKNOWN'),document_code:str=Form(''),revision:str=Form(''),approval_status:str=Form('UNKNOWN'),approval_date:str=Form('')):
    ensure_editable(id_)
    if stage not in ('PD','RD','ID') or approval_status not in ('UNKNOWN','DRAFT','APPROVED','FOR_CONSTRUCTION','SUPERSEDED','CANCELLED'):raise ValueError('Неверная стадия или статус')
    if approval_date:
        try:datetime.date.fromisoformat(approval_date)
        except ValueError:raise ValueError('Дата утверждения должна быть YYYY-MM-DD')
    if len(files)>10:raise HTTPException(413,'Не более 10 файлов за одну загрузку')
    staged=[];total=0
    try:
        for file in files:
            name=Path(file.filename or '').name
            if Path(name).suffix.lower() not in SUPPORTED:raise ValueError('Поддерживаются PDF, DOCX, XML, XLSX и изображения')
            dest=DATA/'uploads'/str(uuid.uuid4());dest.mkdir()
            path=dest/re.sub(r'[<>:"/\\|?*]','_',name)
            size=0
            with path.open('wb') as out:
                while block:=await file.read(1024*1024):
                    size+=len(block);total+=len(block)
                    if size>50*1024**2 or total>200*1024**2:raise HTTPException(413,'Лимит 50 МиБ на файл и 200 МиБ на пакет')
                    if shutil.disk_usage(ROOT).free<1024**3:raise HTTPException(507,'Недостаточно места на диске')
                    out.write(block)
            staged.append(path)
            if path.suffix.lower()=='.pdf':
                try:
                    with PDF_LOCK,fitz.open(path) as pdf:
                        if not len(pdf) or pdf.needs_pass:raise ValueError('Пустой PDF или PDF с паролем')
                except fitz.FileDataError:raise ValueError('Повреждённый или некорректный PDF')
        docs=[]
        for path in staged:
            d=register(path,id_,{'stage':stage,'discipline':discipline,'document_code':document_code or path.stem,'revision':revision or None,'approval_status':approval_status,'approval_date':approval_date or None,'metadata':{'origin':'inspector_upload','immutable':True}});docs.append(d['id'])
        audit('upload',id_,{'file_ids':docs},'inspector-local')
        job=enqueue('parse',id_,{'file_ids':docs,'ocr':True})
        return {'file_ids':docs,'job':job}
    except Exception:
        # Quarantined incomplete files remain unregistered; no partial package is indexed.
        raise

@app.post('/api/objects/{id_}/manifest')
def set_manifest(id_,body:ManifestBody):
    ensure_editable(id_)
    for d in body.documents:
        if set(d)-{'stage','document_code','sha256','required','discipline'}:raise ValueError('Манифест содержит неподдерживаемые поля; метки разметки запрещены')
        if d.get('stage') not in ('PD','RD','ID') or not (d.get('document_code') or d.get('sha256')):raise ValueError('Нужны стадия и шифр/хеш')
    execute('UPDATE objects SET manifest=%s WHERE id=%s',({'documents':body.documents},id_));audit('manifest',id_,body.model_dump(),current_user.get());return completeness(id_)
@app.post('/api/objects/{id_}/revisions')
def set_revision(id_,body:ChoiceBody):
    ensure_editable(id_);doc=get_doc(body.file_id)
    if doc['object_id']!=id_ or doc['stage']=='UNKNOWN':raise ValueError('Нужно выбрать документ этого объекта с известной стадией')
    execute('INSERT INTO revision_choices(object_id,stage,document_code,file_id,user_id,reason) VALUES(%s,%s,%s,%s,%s,%s)',(id_,doc['stage'],doc['document_code'],doc['id'],current_user.get(),body.reason))
    audit('revision_choice',id_,body.model_dump(),current_user.get())
    return enqueue('compare',id_,{'codes':affected_parameters([doc['id']]) or None})
@app.post('/api/objects/{id_}/parameters/{code}/applicability')
def applicability(id_,code,body:ApplicabilityBody):
    obj=ensure_editable(id_)
    if not one('SELECT code FROM parameters WHERE code=%s',(code,)):raise ValueError('Неизвестный параметр')
    values=dict(obj['applicability']);values[code]=body.model_dump()
    execute('UPDATE objects SET applicability=%s WHERE id=%s',(values,id_));audit('applicability',id_,{'code':code,**body.model_dump()},current_user.get())
    return enqueue('compare',id_,{'codes':[code]})
@app.get('/api/parameters')
def parameters():return catalog()
@app.get('/api/objects/{id_}/checks')
def checks(id_):return query('SELECT p.*,c.implementation_status,c.completeness_status,c.finding_status,c.details FROM parameters p LEFT JOIN checks c ON c.parameter_code=p.code AND c.object_id=%s ORDER BY p.ordinal',(id_,))
@app.get('/api/objects/{id_}/findings')
def object_findings(id_):
    from .navigation import current_findings
    get_object(id_)
    return current_findings(id_)
@app.post('/api/findings/{id_}/review')
def finding_review(id_,body:ReviewBody):return review(id_,body.status,body.reason_code,body.comment,current_user.get(),body.expert_verified,body.training_consent)
@app.post('/api/findings/{id_}/evidence')
def finding_evidence(id_,body:EvidenceBody):
    from .protocols import correct_evidence
    return correct_evidence(id_,body.evidence,body.reason,current_user.get())

@app.post('/api/findings/{id_}/correction')
def finding_correction(id_,body:CorrectionBody):
    from .protocols import correct_evidence
    f=one('SELECT * FROM findings WHERE id=%s AND active',(id_,))
    if not f:raise HTTPException(404,'Находка не найдена')
    p=one('SELECT * FROM parameters WHERE code=%s',(f['parameter_code'],)) if f['parameter_code'] else None
    evidence=[]
    for item in body.evidence:
        d=get_doc(item.file_id)
        evidence.append({**item.model_dump(exclude={'raw'}),'sha256':d['sha256'],'stage':d['stage'],'document_code':d['document_code'],'revision':d['revision'],'approval_status':d['approval_status'],'entity':f['entity'],'value':scalar(item.raw,p['unit'] if p else ''),'coordinate_space':'visible_rotated_page_normalized'})
    return correct_evidence(id_,evidence,body.reason,current_user.get())
@app.get('/api/objects/{id_}/search')
def object_search(id_,q:str='',stage:str|None=None,file_id:str|None=None,revision:str|None=None,entity:str|None=None,current_only:bool=False):return search(id_,q,stage,file_id,revision,entity,current_only)
@app.get('/api/documents/{id_}/pdf')
def pdf_file(id_):
    with PDF_LOCK:path=document_pdf(get_doc(id_))
    return FileResponse(path,media_type='application/pdf')
@app.get('/api/documents/{id_}/pages/{page}/image')
def page_image(id_,page:int,scale:float=Query(1.2,ge=.25,le=3)):
    doc=get_doc(id_)
    with PDF_LOCK,fitz.open(document_pdf(doc)) as pdf:
        if not 1<=page<=len(pdf):raise HTTPException(404,'Страница не найдена')
        p=pdf[page-1];actual=min(scale,math.sqrt(12_000_000/max(1,p.rect.width*p.rect.height)))
        pix=p.get_pixmap(matrix=fitz.Matrix(actual,actual),alpha=False)
        return Response(pix.tobytes('png'),media_type='image/png',headers={'Cache-Control':'private,max-age=3600'})
@app.get('/api/documents/{id_}/pages/{page}')
def page_data(id_,page:int):
    row=one('SELECT * FROM pages WHERE file_id=%s AND page=%s',(id_,page))
    if not row:raise HTTPException(404,'Страница ещё не обработана')
    return row
@app.post('/api/documents/{id_}/measure')
def measure(id_,body:MeasureBody):
    from .geometry import measure_segment
    d=get_doc(id_)
    with PDF_LOCK,fitz.open(document_pdf(d)) as pdf:
        if body.page>len(pdf):raise ValueError('Страница вне документа')
        page=pdf[body.page-1]
        result=measure_segment(page.rect.width,page.rect.height,body.reference_points,body.reference_mm,body.measure_points)
    return {'status':'MEASUREMENT_REQUIRES_REVIEW','file_id':id_,'sha256':d['sha256'],'page':body.page,'result':result}
@app.post('/api/documents/{id_}/vision')
def vision(id_,body:CropBody):
    d=get_doc(id_);box=body.bbox
    if not all(math.isfinite(v) and 0<=v<=1 for v in box) or box[0]>=box[2] or box[1]>=box[3]:raise ValueError('Неверная область')
    with PDF_LOCK,fitz.open(document_pdf(d)) as pdf:
        if body.page>len(pdf):raise ValueError('Страница вне документа')
        p=pdf[body.page-1]
        pix=render_region(p,box)
        source_lines=[l for l in pdf_lines(p) if l['bbox'][0]>=box[0] and l['bbox'][1]>=box[1] and l['bbox'][2]<=box[2] and l['bbox'][3]<=box[3]]
    source_method='TEXT_LAYER'
    if not source_lines:
        from PIL import Image
        local_lines=ocr_image(Image.open(io.BytesIO(pix.tobytes('png'))),200)
        source_lines=[{**l,'bbox':[box[0]+l['bbox'][0]*(box[2]-box[0]),box[1]+l['bbox'][1]*(box[3]-box[1]),box[0]+l['bbox'][2]*(box[2]-box[0]),box[1]+l['bbox'][3]*(box[3]-box[1])]} for l in local_lines]
        source_method='TESSERACT_CROP'
    result=call_model(body.prompt,VisionReply,'vision-crop',model=VISION_MODEL,image=pix.tobytes('png'))
    if not result.text.strip() and not any(s.strip() for s in result.observations):result.unreadable=True
    return {'status':'READING_REQUIRES_REVIEW' if not result.unreadable else 'LOW_QUALITY','result':result.model_dump(),'source':{'file_id':id_,'page':body.page,'bbox':box},'source_reading':{'method':source_method,'lines':source_lines},'requires_inspector':True,'absence_proven':False,'execution':last_execution.get()}

@app.get('/api/objects/{id_}/protocol')
def protocol(id_):
    if get_object(id_)['status']=='FINALIZED':return one('SELECT snapshot FROM protocols WHERE object_id=%s ORDER BY version DESC LIMIT 1',(id_,))['snapshot']
    return build_snapshot(id_)
@app.post('/api/objects/{id_}/protocol')
def save_protocol(id_,finalize:bool=False):return snapshot(id_,finalize,current_user.get())
@app.get('/api/objects/{id_}/protocols')
def history(id_):return query('SELECT id,version,status,manifest_hash,created_at FROM protocols WHERE object_id=%s ORDER BY version DESC',(id_,))
@app.get('/api/objects/{id_}/export/{format_}')
def export(id_,format_:Literal['json','pdf'],version:int|None=None):
    if version:
        row=one('SELECT snapshot FROM protocols WHERE object_id=%s AND version=%s',(id_,version))
        if not row:raise HTTPException(404)
        data=row['snapshot']
    else:
        obj=get_object(id_)
        latest=one('SELECT snapshot FROM protocols WHERE object_id=%s ORDER BY version DESC LIMIT 1',(id_,))
        data=latest['snapshot'] if obj['status']=='FINALIZED' and latest else build_snapshot(id_)
    if format_=='json':return Response(json.dumps(data,ensure_ascii=False,indent=2),media_type='application/json',headers={'Content-Disposition':f'attachment; filename="protocol-{id_}.json"'})
    return Response(export_pdf(data),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="protocol-{id_}.pdf"'})
@app.get('/api/objects/{id_}/audit')
def history_audit(id_):return query('SELECT * FROM audit WHERE object_id=%s ORDER BY id DESC LIMIT 200',(id_,))
@app.post('/api/datasets/prepare')
def dataset_prepare():return enqueue('dataset')
@app.get('/api/datasets')
def datasets():return query('SELECT * FROM datasets ORDER BY created_at DESC')
@app.get('/api/integration/objects/{id_}/confirmed')
def rin_export(id_):
    from .integrations import delivery
    obj=get_object(id_)
    if obj['status']!='FINALIZED':raise HTTPException(409,'Протокол не финализирован')
    p=one('SELECT snapshot FROM protocols WHERE object_id=%s ORDER BY version DESC LIMIT 1',(id_,))['snapshot']
    state=delivery(id_) or {}
    return {'connector':'LOCAL_MOCK','signed':False,'sync_status':state.get('status','NOT_SENT'),'receipt':state.get('receipt'),'object_id':id_,'protocol_version':p['version'],'input_manifest_hash':p['input_manifest_hash'],'findings':p['sections']['confirmed_violations']}

@app.get('/api/objects/{id_}/rin')
def rin_status(id_):
    from .integrations import delivery
    get_object(id_)
    return delivery(id_) or {'status':'NOT_SENT'}

@app.post('/api/objects/{id_}/rin')
def rin_send(id_):
    from .integrations import send
    get_object(id_)
    result=send(id_,current_user.get())
    return JSONResponse(result,status_code=200 if result['ok'] else 503)

@app.post('/api/mock-rin/protocols')
async def mock_receive(request:Request):
    from .integrations import receive
    return await asyncio.to_thread(receive,await request.body(),request.headers.get('X-Mock-Signature',''))

@app.get('/api/training/readiness')
def training_readiness():
    from .training import readiness as gold_readiness
    return gold_readiness()

@app.post('/api/training/start')
def training_start():
    from .training import start
    return start()

class DimensionsBody(CropBody):
    parameter_code:str
    entity:str=Field(min_length=1)
    unit:Literal['mm','m']

@app.post('/api/documents/{id_}/dimensions')
def dimensions(id_,body:DimensionsBody):
    from .geometry import inspect_dimensions
    d=get_doc(id_)
    with PDF_LOCK:
        result=inspect_dimensions(document_pdf(d),body.page,body.bbox,body.parameter_code,body.entity,body.unit)
    return {**result,'file_id':id_,'sha256':d['sha256'],'revision':d['revision']}

app.mount('/static',StaticFiles(directory=ROOT/'static'),name='static')
@app.get('/')
def index():return FileResponse(ROOT/'static/index.html')
