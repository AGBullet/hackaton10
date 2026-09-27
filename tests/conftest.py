import uuid
from pathlib import Path
import pytest
import psycopg
from inspector import db
from inspector.config import ROOT,PG
from inspector.catalog import load_catalog

@pytest.fixture
def isolated_db():
    old=db.SCHEMA
    schema='inspector_test_'+uuid.uuid4().hex[:12]
    db.SCHEMA=schema;db.init();load_catalog()
    yield schema
    with psycopg.connect(**PG,autocommit=True) as c:
        assert schema.startswith('inspector_test_')
        c.execute(psycopg.sql.SQL('DROP SCHEMA {} CASCADE').format(psycopg.sql.Identifier(schema)))
    db.SCHEMA=old

@pytest.fixture
def pdf_factory():
    from reportlab.pdfgen import canvas
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    font='C:/Windows/Fonts/arial.ttf'
    if not Path(font).exists():font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    if 'TestRus' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('TestRus',font))
    dest=ROOT/'data'/'test-fixtures'/uuid.uuid4().hex;dest.mkdir(parents=True)
    def make(name,lines):
        p=dest/name;c=canvas.Canvas(str(p));c.setFont('TestRus',12)
        for i,line in enumerate(lines):c.drawString(40,790-i*25,line)
        c.save();return p
    return make

@pytest.fixture
def compared_object(isolated_db,pdf_factory):
    from inspector.registry import create_object,register
    from inspector.extraction import parse_document,extract_facts
    from inspector.catalog import catalog
    from inspector.comparison import compare
    obj=create_object('TEST-'+uuid.uuid4().hex)
    docs=[]
    for stage,area,concrete in [('PD',1000,'B35'),('RD',950,'B25')]:
        p=pdf_factory(stage+'.pdf',[f'Площадь застройки: {area} м²',f'Класс бетона {concrete}; элемент К1'])
        d=register(p,obj,{'stage':stage,'discipline':'АР','document_code':'TEST-'+stage,'revision':'1','approval_status':'APPROVED','approval_date':'2026-08-17','metadata':{'test':True}})
        parse_document(d['id'],allow_ocr=False);extract_facts(d['id'],catalog());docs.append(d)
    compare(obj)
    return obj,docs
