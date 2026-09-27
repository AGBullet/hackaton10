/* The inspector journey. All server authorization is independent of hidden controls. */
let sessionInfo=null,extracted=[],resultMode='findings',activeExtracted=null;
const workMode={PD:false,RD:false,ID:false};
const can=cap=>sessionInfo?.capabilities.includes(cap);
const baseLoadObject=loadObject,baseRenderPanel=renderPanel,baseRenderFindings=renderFindings,baseOpenFinding=openFinding,baseRenderDocuments=renderDocuments,baseRenderMatrix=renderMatrix;

function roleUI(){
 const visible={inspector:['review','documents','search','history','matrix','help'],admin:['documents','history','help'],ml_engineer:['system','matrix','help']};
 const tabs=visible[sessionInfo.role]||visible.inspector;
 for(const tab of ['review','documents','search','history','matrix','system','help']){
  const b=$(`nav [data-tab=${tab}]`);if(b)b.hidden=!tabs.includes(tab);
 }
 $('#user-id').value=sessionInfo.user_id;$('#user-id').readOnly=true;$('#user-id').closest('label').hidden=true;
 $('#role-select').value=sessionInfo.role;
 $('#role-note').textContent=sessionInfo.role_switch_requires_password?'Локальные роли · вход по паролю':'Демонстрационные роли · без проверки личности';
 const restrictions={'#scan':'manage','#dataset':'dataset','#new-object':'prepare','#upload-open':'prepare','#parse-open':'prepare','#compare':'prepare','#assisted':'prepare','#free-search':'prepare','#manifest-open':'prepare','#finalize':'review','#snapshot':'prepare','#manual-open':'manual','#prepare-workflow':'prepare'};
 Object.entries(restrictions).forEach(([s,c])=>{if($(s))$(s).hidden=!can(c)});
 if($('#advanced-processing'))$('#advanced-processing').hidden=!can('prepare');
 $$('[data-choose],[data-applicable],[data-extract]').forEach(b=>b.hidden=!can('prepare'));
 if(!can('review'))$('#review-form').hidden=true;
 installTools();
}

loadObjects=async function(){
 const objects=await api('/objects');
 $('#object-select').innerHTML=objects.map(o=>`<option value="${o.id}">${esc(o.name)} · ПД ${o.stage_counts?.PD||0} / РД ${o.stage_counts?.RD||0} / ИД ${o.stage_counts?.ID||0}</option>`).join('');
 if(!objects.some(o=>o.id===objectId))objectId=objects.filter(o=>o.stage_counts?.PD&&o.stage_counts?.RD&&o.stage_counts?.ID).sort((a,b)=>b.processed_files-a.processed_files)[0]?.id||objects[0]?.id||'';
 $('#object-select').value=objectId;
 if(objectId)await loadObject();else renderEmpty();
};

let objectLoadGeneration=0;
let renderedObjectId=null;
loadObject=async function(){
 if(!objectId)return;
 const id=objectId,generation=++objectLoadGeneration;
 if(renderedObjectId!==id){
  renderedObjectId=id;currentFinding=null;activeExtracted=null;extracted=[];documents=[];allFindings=[];checks=[];objectDetail=null;docPage=1;auditPage=1;resultMode='findings';jobSignature='';activeJob=null;
  Object.keys(panels).forEach(stage=>{workMode[stage]=false;Object.keys(panels[stage]).forEach(k=>delete panels[stage][k]);Object.assign(panels[stage],{page:1,zoom:1});});
  $('#finding-detail').innerHTML='<p>Загрузка выбранного объекта…</p>';$('#review-form').hidden=true;$('#finding-list').innerHTML='';$('#search-results').innerHTML='';$('#protocol-history').innerHTML='';$('#audit-history').innerHTML='';$('#progress-box').hidden=true;
  $$('.page-canvas').forEach(el=>el.innerHTML='');
 }
 const results=await Promise.all(['','/documents?limit=10000','/findings','/checks','/extractions'].map(path=>api('/objects/'+id+path)));
 if(id!==objectId||generation!==objectLoadGeneration)return;
 [objectDetail,documents,allFindings,checks,extracted]=results;
 $('#stat-candidates').textContent=allFindings.filter(f=>!['CONFIRMED_VIOLATION','NEGATIVE_VERIFIED'].includes(f.status)).length;
 $('#stat-confirmed').textContent=objectDetail.summary.confirmed_violations;
 $('#stat-files').textContent=documents.length;
 $('#stat-completeness').textContent=objectDetail.completeness.complete?'Комплект по манифесту получен':'Полнота не подтверждена';
 const frozen=objectDetail.object.status==='FINALIZED';
 ['#upload-open','#parse-open','#compare','#assisted','#free-search','#finalize'].forEach(s=>$(s).disabled=frozen);
 renderDocuments();renderMatrix();
 if(!$('#viewers').children.length)buildViewers();
 updateViewerOptions();
 if(currentFinding){currentFinding=allFindings.find(f=>f.id===currentFinding.id)||null;if(!currentFinding){Object.values(panels).forEach(p=>{p.evidence=null;p.file=null});$('#review-form').hidden=true;$('#finding-detail').innerHTML='<p>Состав доказательств обновился. Откройте актуальную карточку из списка.</p>';updateViewerOptions()}}
 if(!allFindings.length&&extracted.length&&$('#finding-filter').value==='all')resultMode='values';
 if(resultMode==='values'&&!extracted.length)resultMode='findings';
 renderJourney();renderFindings();roleUI();
 if(currentTab==='history')await loadHistory();
};

function renderJourney(){
 const r=objectDetail.readiness;
 $('#stage-overview').innerHTML=['PD','RD','ID'].map(s=>`<button class="stage-chip" data-stage-registry="${s}"><strong>${labels[s]} · ${r.stages[s].files} файлов</strong><small>Извлечён текст: ${r.stages[s].processed} · выбрано для сверки: ${r.stages[s].selected}</small></button>`).join('');
 $$('[data-stage-registry]').forEach(b=>b.onclick=()=>{$('#doc-stage').value=b.dataset.stageRegistry;$('#doc-query').value='';switchTab('documents');renderDocuments()});
 $('#workflow-status').innerHTML=`<strong>${r.processed_files?`Текст извлечён из ${r.processed_files} документов (${r.processed_pages} страниц).`:'Документы загружены; извлечение ещё не запускалось.'}</strong> Найдено значений: ${extracted.length}. Сравнений с доказательствами: ${allFindings.length}.<p>${r.blockers.map(esc).join('<br>')||'Рабочий комплект выбран. Откройте результат и проверьте обе исходные страницы.'}</p>${r.low_quality_pages?`<p class="warning-text">${r.low_quality_pages} страниц низкого качества: их нужно проверить по изображению.</p>`:''}`;
 $('#object-state').innerHTML=objectDetail.object.status==='FINALIZED'?badge('FINALIZED'):`<span class="readiness-label">${r.blockers.length?'Нужна подготовка источников':'Есть рабочий комплект'}</span>`;
 $('#search-coverage').textContent=`Поиск сейчас охватывает ${r.processed_pages} извлечённых страниц выбранного объекта. Файлов с текстом: ${r.processed_files} из ${r.total_files}.`;
 $('#results-findings').textContent=`Расхождения (${allFindings.length})`;
 $('#results-values').textContent=`Найденные значения (${extracted.length})`;
 $('#results-findings').classList.toggle('selected',resultMode==='findings');$('#results-values').classList.toggle('selected',resultMode==='values');
 $('#prepare-workflow').disabled=objectDetail.object.status==='FINALIZED';$('#manual-open').disabled=objectDetail.object.status==='FINALIZED';
}

renderFindings=function(){
 if(resultMode==='values'&&extracted.length){
  $('#finding-list').innerHTML=extracted.map(f=>`<button class="finding-item ${activeExtracted===f.id?'selected':''}" data-value="${f.id}"><span class="code">${esc(f.parameter_code)} · ${labels[f.stage]}</span><h3>${esc(f.parameter_name)}</h3><p class="values">${esc(value(f.value))}</p><span class="value-note">Прочитано в документе · ещё не сравнение</span><small>Стр. ${f.page} · ${esc(f.document_name)}</small></button>`).join('');
  $$('[data-value]').forEach(b=>b.onclick=()=>openValue(b.dataset.value));return;
 }
 baseRenderFindings();
 const filter=$('#finding-filter').value,visible=allFindings.filter(f=>filter==='all'||f.status===filter);
 if(!visible.length){
  const filtered=filter!=='all';
  $('#finding-list').innerHTML=`<div class="empty"><strong>${filtered?'Нет карточек по выбранному фильтру':'Сопоставимых результатов пока нет'}</strong><p>${filtered?'«Проверенные отрицательные» появляются только после решения инспектора.':'Это не означает, что нарушений нет. Посмотрите найденные значения и причину остановки над документами.'}</p>${filtered?'<button id="reset-filter">Показать все результаты</button>':''}${extracted.length?'<button id="show-values">Открыть найденные значения</button>':''}</div>`;
  if($('#reset-filter'))$('#reset-filter').onclick=()=>{$('#finding-filter').value='all';renderFindings()};
  if($('#show-values'))$('#show-values').onclick=()=>{resultMode='values';renderJourney();renderFindings()};
 }
};

function openValue(id){
 const f=extracted.find(x=>x.id===id);if(!f)return;
 currentFinding=null;activeExtracted=id;Object.keys(workMode).forEach(s=>workMode[s]=false);$('#review-form').hidden=true;
 for(const [stage,p] of Object.entries(panels)){p.evidence=null;p.draft=null;if(stage===f.stage){p.file=f.file_id;p.page=f.page;p.evidence=f.evidence;p.zoom=1}renderPanel(stage)}
 $('#finding-detail').innerHTML=`<span class="badge warning">Прочитано в документе</span><h3>${esc(f.parameter_name)}: ${esc(value(f.value))}</h3><p>Это число, которое система нашла на стр. ${f.page} (${labels[f.stage]}). Жёлтая рамка — место в тексте. Пока нет пары из другой стадии, это <b>не нарушение</b>.</p><p class="match-fragment">${esc(f.evidence.quote)}</p>`;
 Object.keys(workMode).forEach(s=>workMode[s]=false);
 renderFindings();
 $('#finding-detail').scrollIntoView({block:'start'});
}
openFinding=function(id){activeExtracted=null;Object.keys(workMode).forEach(s=>workMode[s]=false);Object.values(panels).forEach(p=>p.draft=null);baseOpenFinding(id);roleUI();$('#finding-detail').scrollIntoView({block:'start'});if(currentFinding?.model_version==='provisional-v1'){$('#confirm').disabled=true;$('#reject').disabled=true;$('#finding-detail').insertAdjacentHTML('beforeend','<p class="warning-text">Нужны основания выбора редакций. Откройте «Выбор редакций и ручная проверка» над комплектом. Совпадение значений само по себе не является нарушением.</p>')}else $('#reject').disabled=false};

updateViewerOptions=function(){
 $$('.viewer').forEach(el=>{
  const stage=el.dataset.stage,p=panels[stage],ds=documents.filter(d=>d.stage===stage&&d.parse_status!=='UNSUPPORTED'&&!isServiceFile(d));
  if(p.evidence&&ds.some(d=>d.id===p.evidence.file_id)){p.file=p.evidence.file_id;if(!workMode[stage])p.page=p.evidence.page}
  else if(!workMode[stage])p.file='';
  else if(!ds.some(d=>d.id===p.file))p.file=ds[0]?.id||'';
  el.querySelector('.viewer-file').innerHTML='<option value="">Выберите документ</option>'+ds.map(d=>`<option value="${d.id}">${esc(d.name)}${objectDetail?.selected_file_ids.includes(d.id)?' · выбран':''}</option>`).join('');
  el.querySelector('.viewer-file').value=p.file||'';
  el.dataset.renderedFile=p.file||'';
  renderPanel(stage);
 });
};

function updateWorkButton(stage){
 const el=$(`.viewer[data-stage=${stage}]`);if(!el)return;
 let btn=el.querySelector('.work-toggle');
 if(!btn){btn=document.createElement('button');btn.type='button';btn.className='work-toggle';el.querySelector('.viewer-title').append(btn)}
 btn.textContent=workMode[stage]?'К превью':'Взять в работу';
 btn.onclick=()=>{
  workMode[stage]=!workMode[stage];
  if(workMode[stage]&&!panels[stage].file){const d=documents.find(x=>x.stage===stage&&x.parse_status!=='UNSUPPORTED'&&!isServiceFile(x));if(d)panels[stage].file=d.id}
  updateViewerOptions();
 };
}

renderPanel=function(stage){
 const p=panels[stage],el=$(`.viewer[data-stage=${stage}]`);if(!el)return;
 el.classList.toggle('preview-mode',!workMode[stage]);
 updateWorkButton(stage);
 if(!workMode[stage]&&!p.evidence){
  p.file='';
  el.querySelector('.viewer-file').value='';
  el.querySelector('.page-canvas').innerHTML=`<div class="stage-help"><strong>Нет выделенного фрагмента</strong><p>В превью показываются только страницы с найденным параметром. Чтобы открыть документ и разметить вручную — «Взять в работу».</p></div>`;
  el.querySelector('.viewer-meta').textContent='';
  el.querySelector('.viewer-foot').textContent='';
  installTools();updateToolButtons(stage);return;
 }
 if(!workMode[stage]&&p.evidence){p.file=p.evidence.file_id;p.page=p.evidence.page}
 const key=p.file+':'+p.page;
 if(p.renderKey!==key){p.draft=null;p.actionMode=null;p.measure=null;p.renderKey=key}
 baseRenderPanel(stage);
 if(!p.file){el.querySelector('.page-canvas').innerHTML=`<div class="stage-help"><strong>Нет доступного документа ${labels[stage]}</strong><p>Проверьте комплект в реестре.</p><button class="show-stage">Реестр ${labels[stage]}</button></div>`;el.querySelector('.show-stage').onclick=()=>{$('#doc-stage').value=stage;switchTab('documents');renderDocuments()}}
 const img=el.querySelector('img');if(img){img.draggable=false;img.ondragstart=()=>false;img.onload=()=>updateToolButtons(stage)}
 installTools();drawDraft(stage);updateToolButtons(stage);
};

renderDocuments=function(){baseRenderDocuments();roleUI()};
renderMatrix=function(){baseRenderMatrix();roleUI()};

function toolsHint(stage,text){const el=$(`.viewer[data-stage=${stage}] .tool-hint`);if(el)el.textContent=text}
function updateToolButtons(stage){const el=$(`.viewer[data-stage=${stage}]`);if(!el)return;const p=panels[stage],image=el.querySelector('img');el.querySelectorAll('.tool-row button').forEach(b=>b.disabled=!p.file||!image?.complete||!image.naturalWidth||p.visionBusy);el.querySelector('.tool-row')?.toggleAttribute('hidden',!can('vision')||!workMode[stage]);}
function drawDraft(stage){const el=$(`.viewer[data-stage=${stage}]`);if(!el)return;const c=el.querySelector('.page-canvas');c.querySelectorAll('.draft-box').forEach(x=>x.remove());const b=panels[stage].draft;if(b)c.insertAdjacentHTML('beforeend',`<div class="evidence-box draft-box" style="left:${b[0]*100}%;top:${b[1]*100}%;width:${(b[2]-b[0])*100}%;height:${(b[3]-b[1])*100}%"></div>`)}
function drawMeasure(stage){const c=$(`.viewer[data-stage=${stage}] .page-canvas`),m=panels[stage].measure;c.querySelector('.measure-overlay')?.remove();if(!m)return;const line=points=>points.length>=2?`<line x1="${points[0][0]}" y1="${points[0][1]}" x2="${points[1][0]}" y2="${points[1][1]}" stroke="#d27900" stroke-width="3" vector-effect="non-scaling-stroke"/>`:'';c.insertAdjacentHTML('beforeend',`<svg class="measure-overlay" viewBox="0 0 1 1" preserveAspectRatio="none">${line(m.reference)}${line(m.target)}</svg>`)}
function enterRegion(stage){const p=panels[stage];p.actionMode='region';p.measure=null;$(`.viewer[data-stage=${stage}] .page-canvas`).classList.add('drawing');toolsHint(stage,'Зажмите кнопку мыши и обведите нужный фрагмент. Затем нажмите «Прочитать ИИ» или «Ручная сверка».')}
async function readRegion(stage){
 const p=panels[stage],box=p.draft||(p.evidence?.demo?null:p.evidence?.bbox);
 if(!box){enterRegion(stage);notice('Сначала выделите нужный фрагмент на странице.');return}
 p.visionBusy=true;updateToolButtons(stage);toolsHint(stage,'ИИ читает выделение. При смене модели потребуется дополнительное время.');
 try{const r=await post('/documents/'+p.file+'/vision',{page:p.page,bbox:box});openModal('Что прочитал ИИ',`<p>${r.result.unreadable?'Фрагмент не удалось уверенно прочитать.':'Черновое чтение выделенной области. Сверьте с изображением.'}</p><div class="match-fragment">${esc(r.result.text||'Модель не вернула читаемого текста.')}</div>${r.source_reading?.lines?.length?`<h3>${r.source_reading.method==='TEXT_LAYER'?'Из текстового слоя документа':'Отдельное распознавание Tesseract'}</h3><div class="match-fragment">${esc(r.source_reading.lines.map(l=>l.text).join('\n'))}</div><p>Это отдельный источник чтения; он также требует проверки.</p>`:''}<ul>${r.result.observations.map(s=>`<li>${esc(s)}</li>`).join('')}</ul><p>Это чтение одной вырезки, а не сравнение ПД/РД/ИД. Нарушение автоматически не создаётся.</p>`,async()=>{},'Закрыть');toolsHint(stage,'Чтение готово; выделение сохранено.')}finally{p.visionBusy=false;updateToolButtons(stage)}
}
function enterMeasure(stage){const p=panels[stage];p.actionMode='reference';p.measure={reference:[],target:[],mm:null};$(`.viewer[data-stage=${stage}] .page-canvas`).classList.add('drawing');toolsHint(stage,'Шаг 1/3: щёлкните по двум концам размерной линии с известным размером.');drawMeasure(stage)}
function measurePoint(stage,point){
 const p=panels[stage],m=p.measure,mode=p.actionMode;if(!m)return;
 const list=mode==='reference'?m.reference:m.target;list.push(point);drawMeasure(stage);
 if(list.length!==2){toolsHint(stage,mode==='reference'?'Выберите второй конец известной размерной линии.':'Выберите второй конец измеряемой линии.');return}
 p.actionMode=null;
 if(mode==='reference')openModal('Шаг 2/3 · Известный размер',`<p>Введите размер, подписанный у выбранной линии на чертеже. Координаты вручную вводить не нужно.</p><label>Размер в миллиметрах<input id="known-mm" type="number" min="0.1" step="any" required autofocus></label>`,async()=>{m.mm=Number($('#known-mm').value);p.actionMode='target';toolsHint(stage,'Шаг 3/3: щёлкните по двум концам линии, которую нужно измерить.');},'Продолжить');
 else action(async()=>{const r=await post('/documents/'+p.file+'/measure',{page:p.page,reference_points:m.reference.flat(),reference_mm:m.mm,measure_points:m.target.flat()});$(`.viewer[data-stage=${stage}] .page-canvas`).classList.remove('drawing');toolsHint(stage,`Измерено: ${r.result.measured_mm} мм. Калибровка и проекция требуют проверки.`);openModal('Результат измерения',`<h2>${esc(r.result.measured_mm)} мм</h2><p>Расчёт по опорному размеру ${m.mm} мм. Используйте линии в одной проекции и одном масштабе. Это вспомогательное измерение, не автоматическое заключение.</p>`,async()=>{},'Закрыть')})();
}
function installTools(){
 $$('.viewer').forEach(el=>{
  if(el.dataset.expertTools)return;el.dataset.expertTools='1';const stage=el.dataset.stage,p=panels[stage],canvas=el.querySelector('.page-canvas');
  const vision=el.querySelector('[data-command=vision]');vision.remove();
  const row=document.createElement('div');row.className='tool-row';row.innerHTML='<button type="button" data-tool="region">Выделить область</button><button type="button" data-tool="vision">Прочитать ИИ</button><button type="button" data-tool="measure">Измерить</button><button type="button" data-tool="clear" title="Убрать выделение">Сброс</button>';
  el.querySelector('.viewer-header').append(row);const hint=document.createElement('div');hint.className='tool-hint';hint.setAttribute('aria-live','polite');hint.textContent='Инструменты для ручной проверки фрагмента';el.querySelector('.viewer-header').append(hint);
  row.querySelector('[data-tool=region]').onclick=()=>enterRegion(stage);row.querySelector('[data-tool=vision]').onclick=action(()=>readRegion(stage));row.querySelector('[data-tool=measure]').onclick=()=>enterMeasure(stage);row.querySelector('[data-tool=clear]').onclick=()=>{p.draft=null;p.actionMode=null;p.measure=null;canvas.classList.remove('drawing');drawDraft(stage);drawMeasure(stage);toolsHint(stage,'Выделение сброшено.')};
  let start=null;
  const point=e=>{const img=canvas.querySelector('img');if(!img?.complete||!img.naturalWidth)return null;const r=img.getBoundingClientRect();if(!r.width||!r.height)return null;return [Math.max(0,Math.min(1,(e.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(e.clientY-r.top)/r.height))]};
  const updateRegion=e=>{const end=point(e);if(!start||!end)return;p.draft=[Math.min(start[0],end[0]),Math.min(start[1],end[1]),Math.max(start[0],end[0]),Math.max(start[1],end[1])].map(n=>Math.round(n*1e6)/1e6);drawDraft(stage)};
  canvas.addEventListener('pointerdown',e=>{if(!p.actionMode||e.button!==0)return;const pos=point(e);if(!pos)return;e.preventDefault();if(p.actionMode==='reference'||p.actionMode==='target'){measurePoint(stage,pos);return}start=pos;p.draft=null;canvas.setPointerCapture(e.pointerId);});
  canvas.addEventListener('pointermove',e=>{if(start)updateRegion(e)});
  canvas.addEventListener('pointerup',e=>{if(!start)return;updateRegion(e);start=null;p.actionMode=null;canvas.classList.remove('drawing');if(!p.draft||p.draft[2]-p.draft[0]<.002||p.draft[3]-p.draft[1]<.002){p.draft=null;drawDraft(stage);toolsHint(stage,'Обведите прямоугольник, удерживая кнопку мыши.');return}toolsHint(stage,'Область выделена. Теперь можно прочитать её ИИ или использовать в ручной сверке.');});
  canvas.addEventListener('pointercancel',()=>{start=null;p.actionMode=null;canvas.classList.remove('drawing');p.draft=null;drawDraft(stage)});
  updateToolButtons(stage);
 });
}

function workingSetModal(){
 needObject();const ids=Object.values(panels).map(p=>p.file).filter(Boolean),ds=ids.map(id=>documents.find(d=>d.id===id));
 if(!ds.some(d=>d.stage==='PD')||!ds.some(d=>['RD','ID'].includes(d.stage)))throw Error('Выберите ПД и хотя бы один документ РД или ИД в панелях.');
 openModal('Подготовить результаты по выбранным документам',`<p>Система извлечёт текст и значения, выполнит сравнение и покажет источники. Исходные реквизиты утверждения останутся без изменений.</p><ul>${ds.map(d=>`<li><strong>${labels[d.stage]}</strong> ${esc(d.name)} · ${d.page_count||'неизвестно'} страниц</li>`).join('')}</ul><label>Основание выбора этих редакций<input id="working-reason" required minlength="3" placeholder="Почему для этой сверки выбраны именно эти файлы"></label><label class="check"><input id="working-confirm" type="checkbox" required> Я выбрал документы одного объекта и проверил их назначение для этой сверки</label><label class="check"><input id="working-model" type="checkbox" checked> Дополнить извлечение локальной моделью</label><details><summary>Объём обработки</summary><label>Страниц на документ (0 — весь документ)<input id="working-pages" type="number" min="0" max="10000" value="25"></label><p>Для больших томов продолжайте обработку следующей порцией. Проверенные страницы берутся из кеша.</p></details>`,async()=>{if(!$('#working-confirm').checked)throw Error('Подтвердите рабочий комплект');await post('/objects/'+objectId+'/prepare',{file_ids:ids,reason:$('#working-reason').value,max_pages:Number($('#working-pages').value),use_model:$('#working-model').checked});$('#finding-filter').value='all';resultMode='findings';notice('Проверка запущена. Значения и карточки появятся по мере обработки.');await loadObject()},'Выбрать комплект и проверить');
}

const baseUpdateJobs=updateJobs;
updateJobs=function(jobs){
 baseUpdateJobs(jobs);
 const latest=jobs.find(j=>j.object_id===objectId),el=$('#job-summary');if(!el)return;
 if(!latest){el.textContent='';return}
 const result=['RUNNING','QUEUED'].includes(latest.status)?{}:(latest.result||{}),failed=(result.documents||[]).filter(d=>d.error),warning=result.model?.status==='ERROR';
 el.innerHTML=`<details><summary>Последняя обработка: ${esc(labels[latest.status]||latest.status)} · ${esc(latest.message||'')}</summary><p>${result.documents?`Документов в порции: ${result.documents.length}. Ошибок: ${failed.length}.`:''} ${warning?'Текстовые результаты сохранены, но модель не завершила извлечение: '+esc(result.model.reason):''} ${result.model?.model_pages!==undefined?`Модель проверила страниц: ${result.model.model_pages}, принятых значений: ${result.model.facts_added}, неподтверждённых ответов отклонено: ${result.model.rejected_ungrounded}. Параметров вне этой порции: ${result.model.remaining_parameters}.`:''}</p>${failed.length?'<p>Причины по каждому файлу доступны в составе комплекта. Исправленные файлы можно обработать повторно.</p>':''}</details>`;
 el.querySelector('details').open=false;
 if(!can('prepare')){$('#pause').hidden=true;$$('[data-resume]').forEach(b=>b.hidden=true)}
};

async function manualModal(){
 needObject();const actualStage=panels.ID.draft?'ID':panels.RD.draft?'RD':panels.ID.evidence?'ID':'RD';
 const items=['PD',actualStage].map((stage,i)=>({stage,p:panels[stage],role:i?'actual':'expected'}));
 if(items.some(({p})=>!p.file||!(p.draft||p.evidence?.bbox)))throw Error('Выделите доказательную область в ПД и в РД либо ИД. Можно использовать подсветку открытой карточки.');
 if(items.some(({p})=>!objectDetail.selected_file_ids.includes(p.file)))throw Error('Сначала выберите эти редакции для сверки через «Подготовить результаты».');
 const ps=await api('/parameters');
 for(const item of items){try{const page=await api(`/documents/${item.p.file}/pages/${item.p.page}`),b=item.p.draft||item.p.evidence.bbox;item.quote=page.content.lines.filter(l=>l.bbox[0]<b[2]&&l.bbox[2]>b[0]&&l.bbox[1]<b[3]&&l.bbox[3]>b[1]).map(l=>l.text).join(' ')}catch{item.quote=''}}
 openModal('Ручная сверка по двум источникам',`<label>Что проверяем<select id="manual-code">${ps.map(p=>`<option value="${p.code}">${esc(p.name)} · ${p.code}</option>`).join('')}</select></label><label>Помещение / элемент (для общих показателей оставьте пустым)<input id="manual-entity" value=""></label>${items.map((item,i)=>`<fieldset class="manual-fields"><legend>${i?'Фактическое':'Ожидаемое'} · ${labels[item.stage]} · стр. ${item.p.page}</legend><label>Прочитанное значение<input id="manual-value-${i}" required value="${esc(item.p.evidence?.value?.raw||'')}"></label><label>Цитата из выделенной области<textarea id="manual-quote-${i}" required>${esc(item.quote||item.p.evidence?.quote||'')}</textarea></label></fieldset>`).join('')}<label>Основание ручной сверки<input id="manual-reason" required minlength="3"></label><p>Сохранится предварительная карточка. Окончательное решение принимается отдельно.</p>`,async()=>{const r=await post('/objects/'+objectId+'/findings/manual',{parameter_code:$('#manual-code').value,entity:$('#manual-entity').value.trim()||'OBJECT',evidence:items.map((item,i)=>({file_id:item.p.file,page:item.p.page,bbox:item.p.draft||item.p.evidence.bbox,role:item.role,raw:$('#manual-value-'+i).value,quote:$('#manual-quote-'+i).value})),reason:$('#manual-reason').value});resultMode='findings';$('#finding-filter').value='all';await loadObject();openFinding(r.id)},'Сохранить карточку');
 if(activeExtracted){const f=extracted.find(f=>f.id===activeExtracted);if(f)$('#manual-code').value=f.parameter_code}
}

$('#search-form').onsubmit=action(async e=>{
 e.preventDefault();const searchObject=objectId;const query=$('#search-q').value,q=new URLSearchParams({q:query,current_only:$('#search-current').checked});for(const k of ['entity','stage','revision'])if($('#search-'+k).value)q.set(k,$('#search-'+k).value);
 const rows=await api('/objects/'+needObject()+'/search?'+q),terms=query.toLowerCase().split(/\s+/).filter(x=>x.length>2);if(searchObject!==objectId)return;
 $('#search-results').innerHTML=rows.length?`<p>Найдено страниц: ${rows.length}. Это поиск по тексту; модель на этом шаге не вызывается.</p>`+rows.map((r,i)=>{const matches=r.lines.filter(l=>terms.some(t=>l.text.toLowerCase().includes(t)));r.hit=matches[0]||r.lines[0];return `<article class="result-card"><strong>${esc(r.name)}</strong> ${badge(r.stage)} · стр. ${r.page} · ред. ${esc(r.revision??'не определена')} ${badge(r.quality)}<p class="match-fragment">${esc(matches.slice(0,6).map(l=>l.text).join('\n')||r.text.slice(0,800))}</p><button data-result="${i}">Открыть и подсветить источник</button></article>`}).join(''):`<div class="empty">В извлечённом тексте выбранного объекта нет совпадений. ${$('#search-current').checked?'Попробуйте снять «Только выбранные редакции».':''} ${esc($('#search-coverage').textContent)}</div>`;
 $$('[data-result]').forEach(b=>b.onclick=()=>{const r=rows[Number(b.dataset.result)],p=panels[r.stage];if(!p||searchObject!==objectId)return;Object.values(panels).forEach(x=>{x.evidence=null;x.file=null});Object.keys(workMode).forEach(s=>workMode[s]=false);p.file=r.file_id;p.page=r.page;p.evidence=r.hit?{...r.hit,quote:r.hit.text,page:r.page,file_id:r.file_id}:null;currentFinding=null;$('#review-form').hidden=true;$('#finding-detail').innerHTML=`<h3>Источник из поиска</h3><p>${esc(r.hit?.text||'')} · ${labels[r.stage]}, стр. ${r.page}</p><p>Поисковое совпадение не является расхождением.</p>`;switchTab('review');renderPanel(r.stage)});
});

loadSystem=async function(){
 if(!can('quality'))return;
 const [h,q,jobs,sets]=await Promise.all([api('/health'),api('/quality'),api('/jobs'),api('/datasets')]);
 $('#system-info').innerHTML=`<strong>Локальная обработка · один тяжёлый запрос одновременно</strong><p>Worker: ${h.worker_running?'работает':'остановлен'} · RAM свободно ${h.ram_available_gib} ГиБ · диск ${h.disk_free_gib} ГиБ.</p><p>${esc(q.acceptance_note)}</p><div class="quality-grid"><article><h3>Что измерено сейчас</h3><p>Контрольный OCR на ${q.ocr_controlled_test.metrics?.samples||0} строках: CER ${((q.ocr_controlled_test.metrics?.cer||0)*100).toFixed(2)}%. Не приёмка естественных сканов.</p><pre>${esc(JSON.stringify(q.models,null,2))}</pre></article></div><h3>Реальные вызовы моделей</h3><pre>${esc(JSON.stringify(q.calls,null,2))}</pre>`;
 updateJobs(jobs);$('#datasets').innerHTML=sets.length?sets.map(s=>`<article class="history-card">${esc(s.metadata.dataset_version)} · ${s.metadata.records} примеров<p>${esc(s.metadata.gate)}</p></article>`).join(''):'Проверенная обучающая выборка ещё не подготовлена.';
 $('#archive-history').innerHTML=can('manage')?'<button id="admin-archives">Показать журнал распаковки</button>':'Журнал архивов доступен администратору.';
 if($('#admin-archives'))$('#admin-archives').onclick=action(async()=>{const rows=await api('/archives');$('#archive-history').innerHTML=rows.map(a=>`<div class="history-card">${esc(a.path)} · ${esc(a.status)} · ${a.files} файлов</div>`).join('')});roleUI();
};

async function initExpert(){
 sessionInfo=await api('/session');
 $('.sidebar-bottom').insertAdjacentHTML('beforeend','<label>Роль<select id="role-select"><option value="inspector">Инспектор</option><option value="admin">Администратор</option><option value="ml_engineer">ML-инженер</option></select></label><div id="role-note" class="role-note"></div>');
 $('nav').insertAdjacentHTML('beforeend','<button data-tab="help"><span>Как провести проверку</span></button>');$('nav [data-tab=help]').onclick=()=>switchTab('help');
 $('main footer').insertAdjacentHTML('beforebegin',`<section id="tab-help" class="tab"><h2>Работа инспектора</h2><article class="guide-card"><ol><li>Выберите объект. Счётчики ПД/РД/ИД относятся именно к нему. На Алтуфьевском папка ИД в исходном ZIP пустая; у Новослободской — 99 файлов ИД.</li><li>В трёх панелях выберите документы одного объекта и сопоставимых разделов. Не следует сравнивать общий план с произвольным актом только потому, что стадии разные.</li><li>Нажмите «Подготовить результаты», укажите основание выбора редакций. Сервис последовательно извлечёт текст и значения, дополнит их моделью и выполнит сверку.</li><li>Откройте «Найденные значения» или карточку сравнения: источник подсветится. Отсутствие второй стороны или спорная редакция — причина уточнения, а не нарушение.</li><li>Для ручной проверки выделите фрагменты ПД и РД/ИД. «Прочитать ИИ» читает одну область. «Измерить» — два конца известной линии, её размер, затем два конца измеряемой линии. «Ручная сверка» сохраняет карточку по прочитанным значениям.</li><li>Для карточки сравнения укажите причину и комментарий, подтвердите, отклоните или запросите уточнение. Затем сохраните протокол.</li></ol></article></section>`);
 $('.object-bar').insertAdjacentHTML('afterend','<div id="stage-overview" class="stage-overview"></div><section class="workflow-guide"><div id="workflow-status">Загрузка состава объекта…</div><div class="workflow-actions"><button id="prepare-workflow" class="primary">Подготовить результаты</button><button id="manual-open">Ручная сверка</button><button id="show-guide" class="link">Что делать эксперту</button></div></section>');
 $('#tab-review .workbench').insertAdjacentHTML('beforebegin','<div class="result-tabs"><button id="results-findings">Расхождения</button><button id="results-values">Найденные значения</button></div><p id="result-hint" class="help">Сравнения содержат совпадения, возможные расхождения и запросы уточнения. Найденные значения — просто прочитанные числа, не нарушения. Справа в превью только страница с жёлтой рамкой.</p>');
 $('#matrix-open').onclick=()=>switchTab('matrix');
 if($('#show-service'))$('#show-service').onchange=()=>{docPage=1;renderDocuments()};
 $('#search-form').insertAdjacentHTML('beforebegin','<p id="search-coverage" class="help"></p>');$('#search-current').checked=false;
 $('#search-q').placeholder='Площадь застройки, бетон, помещение 104…';$('#free-search').textContent='Найти гипотезы по источникам';
 $('#parse-open').textContent='Обработка всего комплекта';$('#compare').textContent='Пересчитать готовые значения';$('#compare').className='secondary';
 $('#prepare-workflow').onclick=action(workingSetModal);$('#manual-open').onclick=action(manualModal);$('#show-guide').onclick=()=>switchTab('help');
 $('#results-findings').onclick=()=>{resultMode='findings';renderJourney();renderFindings()};$('#results-values').onclick=()=>{resultMode='values';renderJourney();renderFindings()};
 $('#finding-filter').onchange=()=>{resultMode='findings';renderJourney();renderFindings()};
 $('#workflow-status').insertAdjacentHTML('afterend','<div id="job-summary" aria-live="polite"></div>');
 $('.object-bar').insertAdjacentHTML('beforeend','<details id="advanced-processing"><summary>Дополнительная обработка</summary><div class="workflow-actions"></div></details>');
 $('#advanced-processing .workflow-actions').append($('#parse-open'),$('#compare'));
 $('#doc-query').oninput=$('#doc-stage').onchange=renderDocuments;
 $('#matrix-query').oninput=$('#matrix-status').onchange=renderMatrix;
 $('#role-select').onchange=action(async()=>{const role=$('#role-select').value;const change=async password=>{await post('/session',{role,password});sessionInfo=await api('/session');roleUI();switchTab(role==='inspector'?'review':role==='admin'?'documents':'system');await loadObject()};if(sessionInfo.role_switch_requires_password&&role!=='inspector'){openModal('Локальная роль',`<p>Пароль находится в data/local-accounts.txt на этом компьютере.</p><label>Пароль<input id="role-password" type="password" required></label>`,()=>change($('#role-password').value),'Войти')}else await change('')});
 const oldChange=$('#object-select').onchange;$('#object-select').onchange=async()=>{$('#finding-filter').value='all';extracted=[];activeExtracted=null;await oldChange()};
 roleUI();
}
initExpert().then(boot).catch(e=>notice('Не удалось запустить интерфейс: '+e.message,true));
