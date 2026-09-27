"""Local MVP roles. An implicit inspector session; elevation needs local credentials."""
import os,json,time,hmac,hashlib,base64,secrets
from contextvars import ContextVar
from fastapi import HTTPException
from .config import ROOT

current_user=ContextVar('inspector_user',default='system')
ROLES={
 'inspector':{'label':'Инспектор','user_id':'inspector-local','capabilities':['read','review','prepare','manual','vision']},
 'admin':{'label':'Администратор','user_id':'admin-local','capabilities':['read','manage','unfinalize']},
 'ml_engineer':{'label':'ML-инженер','user_id':'ml-local','capabilities':['read','technical','quality','dataset']},
}

def initialize_credentials():
    from dotenv import dotenv_values
    path=ROOT/'.env';values=dotenv_values(path)
    keys=('APP_AUTH_SECRET','APP_ADMIN_PASSWORD','APP_ML_PASSWORD');added={k:secrets.token_urlsafe(24 if k.endswith('SECRET') else 12) for k in keys if not values.get(k)}
    if added:
        with path.open('a',encoding='utf-8') as out:
            out.write('\n# Local roles; never commit these credentials\n')
            for k,v in added.items():out.write(k+'='+v+'\n')
    values.update(added)
    for k in keys:os.environ[k]=values[k]
    credentials=ROOT/'data/local-accounts.txt'
    credentials.write_text('Локальные роли Инспектор ИИ. Не публиковать.\nИнспектор: локальная сессия без пароля.\nАдминистратор: '+values['APP_ADMIN_PASSWORD']+'\nML-инженер: '+values['APP_ML_PASSWORD']+'\n',encoding='utf-8')

def _secret():
    value=os.getenv('APP_AUTH_SECRET')
    if not value:raise HTTPException(503,'Локальные роли не настроены: выполните scripts/local_accounts.py')
    return value.encode()

def session_token(role):
    payload=base64.urlsafe_b64encode(json.dumps({'role':role,'exp':int(time.time())+28800}).encode()).decode()
    return payload+'.'+hmac.new(_secret(),payload.encode(),hashlib.sha256).hexdigest()

def principal(request):
    role='inspector';token=request.cookies.get('inspector_session','')
    if token:
        try:
            payload,signature=token.rsplit('.',1)
            if hmac.compare_digest(signature,hmac.new(_secret(),payload.encode(),hashlib.sha256).hexdigest()):
                decoded=json.loads(base64.urlsafe_b64decode(payload))
                if decoded['exp']>time.time() and decoded['role'] in ROLES:role=decoded['role']
        except (ValueError,KeyError,TypeError):pass
    return {'role':role,**ROLES[role],'authentication':'LOCAL_MVP','personal_identity_verified':False,'role_switch_requires_password':os.getenv('APP_REQUIRE_ROLE_PASSWORD','0')=='1'}

def required_capability(path,method):
    if path=='/api/mock-rin/protocols':return None # Receiver verifies an HMAC, not a browser role.
    if path.startswith('/api/training'):return 'dataset' if method=='POST' else 'quality'
    if path in ('/api/session','/api/health'):return None
    if path.startswith('/api/quality') or path.startswith('/api/model-calls'):return 'quality'
    if path=='/api/archives':return 'technical'
    if path.startswith('/api/datasets'):return 'dataset' if method=='POST' else 'quality'
    if path=='/api/scan':return 'manage'
    if path.endswith('/unfinalize'):return 'unfinalize'
    if method in ('GET','HEAD','OPTIONS'):return None
    if path.endswith('/review') or path.endswith('/evidence') or path.endswith('/correction') or '/applicability' in path:return 'review'
    if path.endswith('/manual'):return 'manual'
    if path.endswith('/vision') or path.endswith('/measure') or path.endswith('/dimensions'):return 'vision'
    if path.startswith('/api/jobs/'):return 'prepare'
    if path.startswith('/api/objects'):return 'prepare'
    return 'manage'

def authorize(request):
    user=principal(request);need=required_capability(request.url.path,request.method)
    if need and need not in user['capabilities']:
        raise HTTPException(403,'Это действие недоступно роли «'+user['label']+'»')
    request.state.principal=user
    return user

_attempts={}
def login(role,password,peer):
    if role not in ROLES:raise HTTPException(400,'Неизвестная роль')
    if role!='inspector' and os.getenv('APP_REQUIRE_ROLE_PASSWORD','0')=='1':
        recent=[t for t in _attempts.get(peer,[]) if time.time()-t<60]
        if len(recent)>=5:raise HTTPException(429,'Слишком много попыток. Повторите через минуту.')
        expected=os.getenv('APP_ADMIN_PASSWORD' if role=='admin' else 'APP_ML_PASSWORD','')
        if not expected or not hmac.compare_digest(expected,password):
            _attempts[peer]=recent+[time.time()];raise HTTPException(401,'Неверный пароль локальной роли')
        _attempts.pop(peer,None)
    return session_token(role)
