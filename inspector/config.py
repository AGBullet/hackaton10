import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DATA = ROOT / 'data'
for name in ('cache', 'uploads', 'exports', 'benchmarks', 'logs'):
    (DATA / name).mkdir(parents=True, exist_ok=True)
PG = dict(host=os.getenv('PGHOST','127.0.0.1'), port=int(os.getenv('PGPORT','5432')), user=os.getenv('PGUSER','postgres'), password=os.getenv('PGPASSWORD',''), dbname=os.getenv('PGDATABASE','inspector_ai'), connect_timeout=5)
LM_URL = os.getenv('LM_BASE_URL','http://127.0.0.1:1234/v1').rstrip('/')
TEXT_MODEL = os.getenv('LM_TEXT_MODEL','qwen3.5-4b')
VISION_MODEL = os.getenv('LM_VISION_MODEL','qwen3-vl-8b-instruct')
PARSER_VERSION = 'extract-1.0'
MATRIX_VERSION = '1.1-20260817'

def inside_root(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Путь находится вне папки проекта')
    return path
