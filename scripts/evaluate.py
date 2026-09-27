import argparse,json,hashlib
from pathlib import Path
from inspector.metrics import evaluate,ocr_metrics

def main():
    p=argparse.ArgumentParser();p.add_argument('--gold',type=Path,required=True);p.add_argument('--predictions',type=Path,required=True);p.add_argument('--output',type=Path,default=Path('reports/evaluation.json'));args=p.parse_args()
    gold=[json.loads(x) for x in args.gold.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    predictions=[json.loads(x) for x in args.predictions.read_text(encoding='utf-8-sig').splitlines() if x.strip()]
    result=evaluate(gold,predictions)
    result.update(gold_sha256=hashlib.sha256(args.gold.read_bytes()).hexdigest(),predictions_sha256=hashlib.sha256(args.predictions.read_bytes()).hexdigest())
    args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
