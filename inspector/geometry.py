"""Calibrated measurements only: do not invent a drawing scale from page size."""
import math
import re

def inspect_dimensions(path,page_number,bbox,parameter_code,entity,unit):
    """Vector-assisted size reading, never automatic absence or element attribution."""
    import fitz
    if parameter_code not in {'M-030','M-040','M-041','M-058','M-059','M-060','M-061'}:raise ValueError('Для этого параметра векторный поиск размеров пока не реализован')
    if unit not in ('mm','m'):raise ValueError('Укажите единицы по примечанию чертежа: mm или m')
    if not entity.strip() or entity=='OBJECT':raise ValueError('Укажите конкретный элемент или помещение')
    if len(bbox)!=4 or not all(math.isfinite(v) and 0<=v<=1 for v in bbox) or bbox[0]>=bbox[2] or bbox[1]>=bbox[3]:raise ValueError('Неверная область')
    with fitz.open(path) as pdf:
        if not 1<=page_number<=len(pdf):raise ValueError('Страница вне документа')
        p=pdf[page_number-1];w,h=p.rect.width,p.rect.height;crop=fitz.Rect(bbox[0]*w,bbox[1]*h,bbox[2]*w,bbox[3]*h)
        segments=[];all_segments=[]
        for drawing in p.get_drawings():
            for item in drawing['items']:
                if item[0]!='l':continue
                a,b=item[1]*p.rotation_matrix,item[2]*p.rotation_matrix
                rect=fitz.Rect(min(a.x,b.x)-1,min(a.y,b.y)-1,max(a.x,b.x)+1,max(a.y,b.y)+1)
                if not crop.intersects(rect):continue
                if math.dist((a.x,a.y),(b.x,b.y))>=1:all_segments.append((a,b))
                if math.dist((a.x,a.y),(b.x,b.y))<8:continue
                if abs(a.x-b.x)<1.5 or abs(a.y-b.y)<1.5:segments.append((a,b))
        def supported_endpoint(point,a,b):
            horizontal=abs(a.y-b.y)<1.5
            for c,d in all_segments:
                # Extension lines / diagonal ticks must actually reach this endpoint.
                if horizontal and abs(c.y-d.y)<1.5:continue
                if not horizontal and abs(c.x-d.x)<1.5:continue
                dx,dy=d.x-c.x,d.y-c.y;length2=dx*dx+dy*dy
                if not length2:continue
                t=max(0,min(1,((point.x-c.x)*dx+(point.y-c.y)*dy)/length2))
                if math.hypot(point.x-(c.x+t*dx),point.y-(c.y+t*dy))<=2:return True
            return False
        dimensions=[]
        for word in p.get_text('words'):
            raw=word[4].replace('\u00a0','')
            if not re.fullmatch(r'\d{2,6}(?:[.,]\d{1,3})?',raw):continue
            rect=fitz.Rect(word[:4])*p.rotation_matrix
            if not crop.contains(rect):continue
            cx,cy=(rect.x0+rect.x1)/2,(rect.y0+rect.y1)/2;near=[]
            for a,b in segments:
                horizontal=abs(a.y-b.y)<1.5
                inside=min(a.x,b.x)-3<=cx<=max(a.x,b.x)+3 if horizontal else min(a.y,b.y)-3<=cy<=max(a.y,b.y)+3
                distance=abs(cy-a.y) if horizontal else abs(cx-a.x)
                if inside and distance<=max(12,rect.height*2):near.append((distance,a,b))
            if not near:continue
            near=[(distance,a,b) for distance,a,b in sorted(near,key=lambda t:t[0])[:4] if supported_endpoint(a,a,b) and supported_endpoint(b,a,b)]
            if not near:continue
            _,a,b=near[0]
            dimensions.append({'source_text':word[4],'value':float(raw.replace(',','.')),'unit':unit,'bbox':[rect.x0/w,rect.y0/h,rect.x1/w,rect.y1/h],'line':[a.x/w,a.y/h,b.x/w,b.y/h],'endpoint_support':True,'entity':entity,'association_verified':False})
        return {'parameter_code':parameter_code,'page':page_number,'bbox':bbox,'scope':'SELECTED_REGION','vector_segments':len(segments),'dimensions':dimensions[:200],'dimension_candidates_total':len(dimensions),'truncated':len(dimensions)>200,'absence_proven':False,'automatic_conclusion':False,'requires_inspector':True,'unit_basis':'INSPECTOR_INPUT','limitation':'Размерная подпись найдена рядом с линией с опорами на концах. Это кандидат: инспектор проверяет выносные линии, единицы и принадлежность элементу. Толщина и площадь сечения не вычисляются без подтверждения геометрии. Отсутствие в области не означает отсутствия на листе.'}

def measure_segment(width,height,reference_points,reference_mm,measure_points):
    if not math.isfinite(reference_mm) or reference_mm<=0:raise ValueError('Нужна положительная длина размерной линии')
    for pts in (reference_points,measure_points):
        if len(pts)!=4 or not all(math.isfinite(v) and 0<=v<=1 for v in pts):raise ValueError('Координаты должны быть в пределах 0…1')
    def distance(p):return math.hypot((p[2]-p[0])*width,(p[3]-p[1])*height)
    reference=distance(reference_points)
    if reference<2:raise ValueError('Слишком короткая опорная линия для надёжной калибровки')
    measured=distance(measure_points)
    return {'measured_mm':round(measured/reference*reference_mm,3),'reference_mm':reference_mm,'reference_points':reference_points,'measure_points':measure_points,'method':'calibrated_euclidean','uncertainty':'Зависит от точности опорного размера и выбора концов линий; проверяет инспектор'}
