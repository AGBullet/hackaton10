import hashlib, json, re
import openpyxl
from .config import ROOT, MATRIX_VERSION
from .db import connection, params, query

# These aliases identify document labels, never GOLD answers.
ALIASES = {
 1:['площадь застройки'],2:['общая площадь здания','общая площадь'],3:['полезная площадь','расчетная площадь'],
 4:['строительный объем здания','строительный объем общий','строительный объем'],5:['объем подземной части'],6:['объем надземной части'],
 7:['количество надземных этажей','количество этажей','этажность'],8:['высота здания'],9:['абсолютная отметка'],10:['количество квартир'],
 12:['количество машино-мест'],13:['вместимость','количество мест'],14:['расчетная электрическая мощность','расчетная мощность'],
 16:['расход водопотребления'],17:['тепловая нагрузка'],18:['часовой расход газа'],19:['коэффициент застройки'],20:['коэффициент использования территории'],
 21:['класс энергетической эффективности'],22:['степень огнестойкости'],23:['класс конструктивной пожарной опасности'],
 25:['площадь асфальтобетонного покрытия'],26:['площадь плиточного покрытия'],27:['площадь озеленения'],
 30:['ширина проезда','ширина пожарного проезда'],31:['радиус поворота'],37:['количество парковочных мест'],38:['количество мест для мгн'],
 40:['ширина коридора','ширина эвакуационного коридора'],41:['ширина двери','ширина дверного проема','ширина эвакуационного выхода'],
 42:['высота проема','высота коридора'],47:['глубина тамбура'],49:['высота ограждения'],
 55:['класс бетона','класс прочности бетона','бетон класса'],56:['марка стали'],57:['класс арматуры','арматура класса'],
 58:['толщина фундаментной плиты','фундаментная плита толщиной'],59:['толщина плиты перекрытия','плита перекрытия толщиной'],61:['толщина несущей стены'],
 62:['диаметр рабочей арматуры','диаметр арматуры'],82:['продолжительность строительства','продолжительность этапа'],
 86:['численность персонала'],103:['предел огнестойкости двери','огнестойкость двери'],104:['ширина эвакуационного прохода'],105:['ширина наружной двери'],
 116:['ширина коридора для мгн'],117:['ширина дверного проема для мгн'],118:['высота порога'],
 124:['класс энергетической эффективности здания'],125:['толщина утеплителя стен','толщина теплоизоляции стен'],
 126:['коэффициент теплопроводности'],127:['сопротивление теплопередаче окон'],128:['толщина утеплителя кровли'],
 131:['удельный годовой расход тепловой энергии'],132:['сметная стоимость строительства','итоговая стоимость']
}

def load_catalog():
    path = next((ROOT/'docs').glob('Матрица*.xlsx'))
    w = openpyxl.load_workbook(path, data_only=True)
    rows = []
    for r in w.active.iter_rows(min_row=2, values_only=True):
        if not isinstance(r[0], int):
            continue
        n,code,section,name,unit,pd,rd,id_,trigger,priority = r[:10]
        # Normative constants in the source matrix are not applied: compare with PD only.
        cfg = {'aliases': ALIASES.get(n, [name.lower()]), 'extractor': 'anchored' if n in ALIASES else 'assisted',
               'comparator':'pd_decrease' if n in (3,12,13,27,28,30,31,37,38,40,41,42,47,49,58,59,60,61,103,104,105,112,116,117,125,127,128) else 'pd_increase' if n in (8,14,16,17,18,19,20,118,126,131,132) else 'pd_difference', 'relative_tolerance':{2:.01,24:.05,25:.05,67:.02,82:.10,93:.05,132:.05}.get(n,0),
               'scope':'OBJECT' if n <= 28 or n in [37,38,82,86,124,131,132] else 'ELEMENT',
               'requires': ['PD','RD_OR_ID'], 'normative_check':False,
               'graphic_interpretation':'DIMENSION_ASSISTED' if n in (30,40,41,58,59,60,61) else 'NOT_IMPLEMENTED',
               'automatic_absence_detection':'NOT_IMPLEMENTED',
               'coverage_scope':'LITERAL_VALUE_COMPARISON',
               'limitation':'Текстовые значения и подписи; геометрия без размерной привязки требует инспектора.'}
        rows.append(dict(code=code,ordinal=n,name=name,section=section,unit=unit or '—',source_pd=pd,source_rd=rd,source_id=id_,trigger_text=trigger,priority=str(priority).split(' ')[0],config=cfg,matrix_version=MATRIX_VERSION))
    if len(rows)!=132 or len({r['code'] for r in rows})!=132:
        raise ValueError('Матрица должна содержать 132 уникальных параметра')
    with connection() as c:
        for r in rows:
            c.execute('INSERT INTO parameters(code,ordinal,name,section,unit,source_pd,source_rd,source_id,trigger_text,priority,config,matrix_version) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(code) DO UPDATE SET config=excluded.config,matrix_version=excluded.matrix_version', params(r.values()))
    (ROOT/'reports'/'matrix_coverage.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
    return rows

def catalog():
    return query('SELECT * FROM parameters ORDER BY ordinal')
