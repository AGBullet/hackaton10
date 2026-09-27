"""Offline metrics only. GOLD never enters the application search index."""
import math,re,unicodedata

def normalized(text):return re.sub(r'\s+',' ',unicodedata.normalize('NFC',text)).strip()
def levenshtein(a,b):
    if len(a)<len(b):a,b=b,a
    row=list(range(len(b)+1))
    for i,x in enumerate(a,1):
        new=[i]
        for j,y in enumerate(b,1):new.append(min(new[-1]+1,row[j]+1,row[j-1]+(x!=y)))
        row=new
    return row[-1]
def ocr_metrics(pairs):
    chars=words=ced=wed=0;covered=0
    for reference,prediction in pairs:
        ref=normalized(reference);pred=normalized(prediction)
        chars+=len(ref);words+=len(ref.split());ced+=levenshtein(ref,pred);wed+=levenshtein(ref.split(),pred.split());covered+=bool(pred)
    return {'cer':ced/chars if chars else None,'character_accuracy':1-ced/chars if chars else None,'wer':wed/words if words else None,'coverage':covered/len(pairs) if pairs else None,'samples':len(pairs),'reference_characters':chars}
def iou(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-intersection
    return intersection/union if union>0 else 0
def wilson(success,total):
    if not total:return None
    z=1.96;p=success/total;denom=1+z*z/total
    center=(p+z*z/(2*total))/denom;half=z*math.sqrt(p*(1-p)/total+z*z/(4*total*total))/denom
    return [max(0,center-half),min(1,center+half)]
def group_key(r):return (r['object_id'],r.get('parameter_code') or r['rule_code'],r['entity'])
def evidence_match(a,b):
    aa=a.get('current_evidence',a.get('evidence',[]));bb=b.get('current_evidence',b.get('evidence',[]))
    return bool(bb) and all(any(x.get('sha256')==y.get('sha256') and x['page']==y['page'] and x['stage']==y['stage'] and iou(x['bbox'],y['bbox'])>=.5 for x in aa) for y in bb)
def evaluate(gold,predictions):
    eligible=[g for g in gold if g['status'] in ('CONFIRMED_VIOLATION','NEGATIVE_VERIFIED')]
    positives=[g for g in eligible if g['status']=='CONFIRMED_VIOLATION'];negatives=[g for g in eligible if g['status']=='NEGATIVE_VERIFIED']
    predicted=[p for p in predictions if p['status'] in ('CANDIDATE','CONFIRMED_VIOLATION')]
    used=set();tp=0
    for p in predicted:
        for i,g in enumerate(positives):
            if i not in used and group_key(p)==group_key(g) and evidence_match(p,g):used.add(i);tp+=1;break
    fp=len(predicted)-tp;fn=len(positives)-tp
    neg_fp=sum(any(group_key(p)==group_key(g) for p in predicted) for g in negatives)
    precision=tp/(tp+fp) if tp+fp else None;recall=tp/(tp+fn) if tp+fn else None
    return {'tp':tp,'fp':fp,'fn':fn,'precision':precision,'recall':recall,'f1':2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else None,'false_positive_rate':neg_fp/len(negatives) if negatives else None,'precision_ci95':wilson(tp,tp+fp),'recall_ci95':wilson(tp,tp+fn),'gold_groups':len(eligible),'gold_positive':len(positives),'gold_negative':len(negatives),'gold_excluded_unverified':len(gold)-len(eligible),'abstention_count':sum(p['status'] in ('MISSING_EVIDENCE','CLARIFICATION_REQUIRED','NOT_COMPARABLE','LOW_QUALITY') for p in predictions),'acceptance_established':False,'note':'Пороги приёмки нельзя подтвердить на малом/несбалансированном наборе. Матч требует объект, параметр/правило, элемент, SHA-256, стадию, страницу и IoU >= 0.5.'}
