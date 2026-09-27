"""Operational coverage, independent of inspector decisions."""
from collections import Counter
from functools import lru_cache
from .config import ROOT
from .db import query, one
from .catalog import catalog

# Reviewed scalar predicates. Generic LLM extraction is not a full matrix rule.
SCALAR_RULES = {2,7,8,9,10,12,13,14,16,17,18,19,20,24,25,26,27,28,30,31,37,38,
                40,41,42,47,49,58,59,60,61,62,82,93,103,104,105,112,116,117,118,
                125,126,127,128,131,132}
REASONS = {'CATALOG_MISSING':'Отсутствует в каталоге',
    'METHOD_NOT_IMPLEMENTED':'Полный метод проверки не реализован',
    'NOT_RUN':'Извлечение для параметра ещё не запускалось',
    'MISSING_SOURCES':'Недостаточно сопоставимых источников или не выбраны редакции',
    'NOT_APPLICABLE':'Неприменимость подтверждена с основанием',
    'COMPARED':'Есть сравнение с доказательствами — требуется решение инспектора'}

@lru_cache(maxsize=1)
def matrix_source():
    import openpyxl
    path=next((ROOT/'docs').glob('Матрица*.xlsx'))
    book=openpyxl.load_workbook(path,read_only=True,data_only=True)
    rows=[{'ordinal':r[0],'code':r[1],'section':r[2],'name':r[3],'unit':r[4] or '—','trigger_text':r[8]} for r in book.active.iter_rows(min_row=2,values_only=True) if isinstance(r[0],int)]
    book.close()
    return rows

def document_reason(d):
    if d['parse_status']=='UNSUPPORTED':
        if d['name'].startswith('~$'):return 'Служебный файл блокировки Office; не содержит документа'
        if d['format'] in ('.doc','.xls'):return 'Старый формат Office: нужен отдельный конвертер; исходник сохранён'
        if d['format']=='.dwg':return 'DWG не поддерживается; для проверки нужен экспорт в PDF'
        return 'Служебный или неподдерживаемый формат: '+(d['format'] or 'без расширения')
    if d['parse_status']=='ERROR':
        error=str(d['quality'].get('error',''))
        if 'empty file' in error:return 'Пустой исходный файл: нет страниц для извлечения'
        if 'zip file' in error:return 'Повреждённый DOCX или неверное расширение: документ не открывается'
        if 'NUL' in error:return 'Ошибка записи текстового слоя; файл ожидает повторной обработки после исправления'
        return 'Не удалось прочитать исходник; подробности сохранены в журнале'
    if d['parse_status']=='PARTIAL':return 'Есть необработанные страницы или ошибки OCR'
    if d['parse_status']=='PENDING':return 'Обработка файла ещё не выполнялась'
    if d['parse_status']=='PARSING':return 'Файл сейчас обрабатывается'
    return 'Все страницы извлечены; это не подтверждение отсутствия нарушений'

def object_coverage(object_id):
    docs=query('SELECT id,name,stage,format,parse_status,page_count,parsed_pages,quality FROM documents WHERE object_id=%s',(object_id,))
    counts=Counter(d['parse_status'] for d in docs)
    checks={r['parameter_code']:r for r in query('SELECT * FROM checks WHERE object_id=%s',(object_id,))}
    obj=one('SELECT applicability FROM objects WHERE id=%s',(object_id,))
    runs=query("SELECT details FROM audit WHERE object_id=%s AND action='parameter_extraction' ORDER BY id",(object_id,))
    attempted={r['details']['code']:r['details'] for r in runs}
    rows=[]
    registered={p['code']:p for p in catalog()}
    source=matrix_source();differences=[]
    for original in source:
        p=registered.get(original['code'])
        if p is None:
            rows.append({**original,'reason':'CATALOG_MISSING','reason_label':REASONS['CATALOG_MISSING']})
            continue
        for field in ('ordinal','section','name','unit','trigger_text'):
            if p[field]!=original[field]:differences.append({'code':p['code'],'field':field,'source':original[field],'catalog':p[field]})
        c=checks.get(p['code']);application=(obj['applicability'] or {}).get(p['code'],{})
        if application.get('applicable') is False:reason='NOT_APPLICABLE'
        elif p['ordinal'] not in SCALAR_RULES:reason='METHOD_NOT_IMPLEMENTED'
        elif c and c['completeness_status']=='COMPLETE':reason='COMPARED'
        elif not c or (p['config']['extractor']=='assisted' and p['code'] not in attempted):reason='NOT_RUN'
        else:reason='MISSING_SOURCES'
        rows.append({'code':p['code'],'name':p['name'],'reason':reason,'reason_label':REASONS[reason],
                     'finding_status':c['finding_status'] if c else None,'execution':attempted.get(p['code']),
                     'literal_extraction':p['config']['extractor'],
                     'limitation':'Принадлежность элемента, согласование изменений и полноту листа проверяет инспектор',
                     'details':c['details'] if c else {}})
    pages=query('SELECT p.method,p.quality,count(*) AS n,sum(p.seconds) AS extraction_seconds FROM pages p JOIN documents d ON d.id=p.file_id WHERE d.object_id=%s GROUP BY 1,2',(object_id,))
    jobs=query('SELECT id,kind,status,progress,total,message,created_at,updated_at FROM jobs WHERE object_id=%s ORDER BY created_at DESC LIMIT 20',(object_id,))
    return {'documents':{'total':len(docs),'processed':counts['PARSED'],'errors':counts['ERROR'],
                         'skipped':counts['UNSUPPORTED'],'partial':counts['PARTIAL'],
                         'remaining':counts['PENDING']+counts['PARSING'],
                         'supported':len(docs)-counts['UNSUPPORTED'],
                         'coverage':counts['PARSED']/max(1,len(docs)-counts['UNSUPPORTED'])},
            'files':[dict(d,reason=document_reason(d)) for d in docs],
            'pages':pages,'jobs':jobs,'parameters':rows,
            'matrix_audit':{'source_parameters':len(source),'catalog_parameters':len(registered),'missing':[r['code'] for r in rows if r['reason']=='CATALOG_MISSING'],'differences':differences,'extra_codes':sorted(set(registered)-{r['code'] for r in source})},
            'parameter_counts':dict(Counter(r['reason'] for r in rows)),
            'quality_metrics':{'ocr_cer':None,'precision':None,'recall':None,'f1':None,
                               'reason':'Для этого объекта нет независимого проверенного эталона; оценка качества не установлена'}}
