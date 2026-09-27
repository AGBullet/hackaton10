"""Local, idempotent mock РиН transport. No production endpoint or digital signature."""
import hashlib,hmac,json,os
import httpx
from .db import connection,params,one,audit
from .access import _secret

def encoded(payload):return json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def signature(content):return hmac.new(_secret(),content,hashlib.sha256).hexdigest()

def receive(content,supplied_signature):
    if not hmac.compare_digest(signature(content),supplied_signature):raise ValueError('Не подтверждён отправитель тестового протокола')
    payload=json.loads(content);key=payload['idempotency_key']
    digest=hashlib.sha256(content).hexdigest()
    receipt={'accepted':True,'receipt_id':'MOCK-'+digest[:16],'idempotency_key':key,'payload_hash':digest,'signed':False}
    with connection() as c:
        c.execute('INSERT INTO mock_rin_inbox(idempotency_key,payload_hash,receipt) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',params((key,digest,receipt)))
        row=c.execute('SELECT * FROM mock_rin_inbox WHERE idempotency_key=%s',(key,)).fetchone()
        if row['payload_hash']!=digest:raise ValueError('Содержимое уже отправленной версии изменилось')
        return row['receipt']

def delivery(object_id):
    return one('SELECT r.* FROM rin_deliveries r JOIN protocols p ON p.id=r.protocol_id WHERE p.object_id=%s ORDER BY p.version DESC LIMIT 1',(object_id,))

def send(object_id,user):
    with connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(89132353)')
        p=c.execute("SELECT * FROM protocols WHERE object_id=%s AND status='PROTOCOL_FINALIZED' ORDER BY version DESC LIMIT 1",(object_id,)).fetchone()
        obj=c.execute('SELECT status FROM objects WHERE id=%s',(object_id,)).fetchone()
        if not p or not obj or obj['status']!='FINALIZED':raise ValueError('Сначала завершите проверку и зафиксируйте протокол')
        previous=c.execute('SELECT * FROM rin_deliveries WHERE protocol_id=%s',(p['id'],)).fetchone()
        if previous and previous['status']=='SENT':return {'ok':True,'message':'Успешно отправлено в тестовый РиН','receipt':previous['receipt'],'duplicate':True}
        payload={'idempotency_key':f"{object_id}:{p['version']}:{p['manifest_hash']}",'protocol':p['snapshot'],'connector':'LOCAL_MOCK','signed':False}
        content=encoded(payload);digest=hashlib.sha256(content).hexdigest();receipt={};error=None
        try:
            # Fixed loopback destination; never send the corpus to an external service.
            port=int(os.getenv('MOCK_RIN_PORT','8000'))
            response=httpx.post(f'http://127.0.0.1:{port}/api/mock-rin/protocols',content=content,headers={'Content-Type':'application/json','X-Mock-Signature':signature(content)},timeout=15,trust_env=False)
            response.raise_for_status();receipt=response.json()
            if receipt.get('accepted') is not True or receipt.get('payload_hash')!=digest or receipt.get('idempotency_key')!=payload['idempotency_key']:raise ValueError('Ответ тестового РиН не соответствует отправленной версии')
        except (httpx.HTTPError,ValueError) as exc:error=str(exc)
        status='ERROR' if error else 'SENT'
        c.execute('''INSERT INTO rin_deliveries(protocol_id,status,receipt,payload_hash,attempts,last_error) VALUES(%s,%s,%s,%s,1,%s)
        ON CONFLICT(protocol_id) DO UPDATE SET status=excluded.status,receipt=excluded.receipt,payload_hash=excluded.payload_hash,attempts=rin_deliveries.attempts+1,last_error=excluded.last_error,updated_at=now()''',params((p['id'],status,receipt,digest,error)))
        c.execute('INSERT INTO rin_attempts(protocol_id,status,details,user_id) VALUES(%s,%s,%s,%s)',params((p['id'],status,{'error':error,'payload_hash':digest},user)))
    audit('rin_delivery',object_id,{'status':status,'version':p['version']},user)
    return {'ok':not error,'message':'Тестовый РиН недоступен или отклонил протокол. Версия сохранена; повторите отправку.' if error else 'Успешно отправлено в тестовый РиН','receipt':receipt,'retryable':bool(error)}
