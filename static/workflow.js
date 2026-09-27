/* Findings navigation and completed inspector actions. Counters come from current server evidence. */
Object.assign(labels,{HIGH:'Высокий приоритет',MEDIUM:'Средний приоритет',LOW:'Низкий приоритет',OBJECT:'Объект в целом',DONE_WITH_ERRORS:'Завершено частично',PARSED:'Текст извлечён',UNSUPPORTED:'Пропущен'});
labels.NO_TRIGGER_PENDING_REVIEW='Отличие без срабатывания правила';
$('#finding-filter').insertAdjacentHTML('beforeend','<option value="NO_TRIGGER_PENDING_REVIEW">Отличия без срабатывания правила</option>');
const previousJourney=renderJourney;
renderJourney=function(){previousJourney();$('#results-findings').textContent=`Сравнения и гипотезы (${allFindings.length})`};
const workflowRenderMatrix=renderMatrix;
renderMatrix=function(){workflowRenderMatrix();$$('#matrix-rows [data-extract]').forEach(b=>{const p=checks.find(x=>x.code===b.dataset.extract);b.closest('td').insertAdjacentHTML('beforeend',`<small>${p?.config.graphic_interpretation==='DIMENSION_ASSISTED'?'Размеры: вспомогательный инструмент с проверкой инспектора.':'Автоматическая геометрическая проверка не реализована.'} Обнаружение отсутствующих элементов не реализовано.</small>`)})};
const findingCategories={possible:'Возможные расхождения',confirmed:'Подтверждённые нарушения',clarification:'Требует уточнения'};
const docFilters=$('#tab-documents .filters');
docFilters.insertAdjacentHTML('beforeend',`<select id="doc-category" aria-label="Фильтр находок"><option value="">Все документы</option><option value="any">Есть находки</option>${Object.entries(findingCategories).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}<option value="unreviewed">Проверка не завершена</option></select><select id="doc-sort" aria-label="Порядок документов"><option value="name">По названию</option><option value="findings">Больше находок</option>${Object.entries(findingCategories).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select>`);
const previousVisibleDocuments=visibleDocuments;
visibleDocuments=function(){
 const category=$('#doc-category').value,sort=$('#doc-sort').value;
 return previousVisibleDocuments().filter(d=>!category||(category==='unreviewed'?d.review_state==='NOT_REVIEWED':(d.finding_counts?.[category==='any'?'total':category]||0)>0)).sort((a,b)=>sort==='name'?a.name.localeCompare(b.name,'ru'):((b.finding_counts?.[sort==='findings'?'total':sort]||0)-(a.finding_counts?.[sort==='findings'?'total':sort]||0)||a.name.localeCompare(b.name,'ru')));
};
$('#doc-category').onchange=$('#doc-sort').onchange=()=>{docPage=1;renderDocuments()};
function badgesForDocument(d){return `<div class="document-findings">${Object.entries(findingCategories).map(([k,v])=>`<button type="button" class="finding-badge ${k}" data-doc-findings="${d.id}" data-category="${k}" ${d.finding_counts?.[k]?'':'disabled'}>${v}: ${d.finding_counts?.[k]||0}</button>`).join('')}</div>`}
function bindFindingNavigation(root){root.querySelectorAll('[data-doc-findings]').forEach(b=>b.onclick=action(()=>openDocumentFindings(b.dataset.docFindings,b.dataset.category)))}
async function openDocumentFindings(fileId,category){
 const sourceObject=objectId;const rows=await api('/documents/'+fileId+'/findings?category='+category);if(sourceObject!==objectId)return;
 const open=f=>{if(sourceObject!==objectId)return;if($('#modal').open)$('#modal').close();resultMode='findings';$('#finding-filter').value='all';switchTab('review');openFinding(f.id);const ev=f.document_evidence[0];if(ev&&panels[ev.stage]){workMode[ev.stage]=false;Object.assign(panels[ev.stage],{file:ev.file_id,page:ev.page,evidence:ev});renderPanel(ev.stage)}};
 if(!rows.length){await loadObject();notice('Список обновлён. По этому фильтру находок больше нет.');return}
 if(rows.length===1){open(rows[0]);return}
 openModal('Находки в документе',rows.map((f,i)=>`<button type="button" class="result-card" data-related-finding="${i}"><strong>${esc(f.parameter_name||'Гипотеза')}</strong><p>${esc(value(f.expected_value))} → ${esc(value(f.actual_value))}</p>${badge(f.status)} · стр. ${f.document_evidence.map(e=>e.page).join(', ')}</button>`).join(''),async()=>{},'Закрыть');
 $$('[data-related-finding]').forEach(b=>b.onclick=()=>open(rows[Number(b.dataset.relatedFinding)]));
}
const previousRenderDocuments=renderDocuments;
renderDocuments=function(){previousRenderDocuments();$$('#document-rows [data-open-doc]').forEach(b=>{const d=documents.find(d=>d.id===b.dataset.openDoc);if(!d)return;const row=b.closest('tr');row.cells[0].insertAdjacentHTML('beforeend',badgesForDocument(d));row.cells[3].insertAdjacentHTML('beforeend',`<small>${esc(d.review_label)}</small>${d.parse_status!=='PARSED'?`<small>${esc(d.processing_reason||'')}</small>`:''}`)});bindFindingNavigation($('#document-rows'))};
const previousRenderPanel=renderPanel;
renderPanel=function(stage){previousRenderPanel(stage);const d=documents.find(d=>d.id===panels[stage].file),el=$(`.viewer[data-stage=${stage}]`);if(!el)return;let box=el.querySelector('.viewer-findings');if(!box){box=document.createElement('div');box.className='viewer-findings';el.querySelector('.viewer-header').append(box)}box.innerHTML=d?badgesForDocument(d):'';bindFindingNavigation(box);};
const previousViewerOptions=updateViewerOptions;
updateViewerOptions=function(){previousViewerOptions();$$('.viewer-file option').forEach(o=>{const d=documents.find(d=>d.id===o.value);if(d?.finding_counts?.total)o.textContent=d.name+` · находок: ${d.finding_counts.total}`})};

$('#finalize').insertAdjacentHTML('afterend',' <button id="send-rin" class="primary">Отправить в тестовый РиН</button>');
$('#protocol-history').insertAdjacentHTML('beforebegin','<p id="rin-state" role="status" class="help"></p>');
$('#dataset').insertAdjacentHTML('afterend',' <button id="train-model" class="primary">Обучить модель</button>');
$('#datasets').insertAdjacentHTML('beforebegin','<div id="training-state" role="status"></div>');
$('#review-form .review-actions').insertAdjacentHTML('beforebegin','<details class="gold-attestation"><summary>Экспертная проверка для обучающей выборки</summary><p>Локальная роль не удостоверяет личность эксперта. Отмечайте только после содержательной проверки источников. Это не независимая приёмка.</p><label class="check"><input id="expert-verified" type="checkbox"> Значения, элемент, редакции и области доказательств проверены экспертом</label><label class="check"><input id="training-consent" type="checkbox"> Подтверждено разрешение использовать источники и решение для обучения</label></details>');
const previousRoleUI=roleUI;
roleUI=function(){previousRoleUI();$('#send-rin').hidden=!can('review');$('#send-rin').disabled=objectDetail?.object.status!=='FINALIZED';$('#train-model').hidden=!can('dataset')};
const workflowRoleUI=roleUI;
roleUI=function(){workflowRoleUI();const frozen=objectDetail?.object.status==='FINALIZED';$$('[data-choose],[data-applicable],[data-extract],#manifest-open,#snapshot').forEach(b=>b.disabled=frozen);$$('[data-dimensions]').forEach(b=>b.hidden=!can('vision'))};
const previousHistory=loadHistory;
loadHistory=async function(){const historyObject=objectId;await previousHistory();if(!objectId||historyObject!==objectId)return;try{const result=await api('/objects/'+objectId+'/rin');if(historyObject!==objectId)return;$('#rin-state').textContent=result.status==='SENT'?'Успешно отправлено в тестовый РиН':result.status==='ERROR'?'Предыдущая отправка не удалась. Нажмите кнопку, чтобы повторить.':'Отправка доступна после завершения проверки. Это тестовый контур без УКЭП.';$('#send-rin').textContent=result.status==='ERROR'?'Повторить отправку в тестовый РиН':'Отправить в тестовый РиН';$('#send-rin').disabled=objectDetail?.object.status!=='FINALIZED'}catch(e){notice(e.message,true)}};
$('#send-rin').onclick=action(async()=>{const button=$('#send-rin');button.disabled=true;try{const result=await post('/objects/'+needObject()+'/rin');$('#rin-state').textContent=result.message;notice(result.message);await loadHistory()}catch(error){await loadHistory();throw error}finally{button.disabled=objectDetail?.object.status!=='FINALIZED'}});
$('#train-model').onclick=action(async()=>{const button=$('#train-model');button.disabled=true;try{const result=await post('/training/start');$('#training-state').innerHTML=result.started?'<p>Обучение поставлено в очередь. Публикация модели потребует отдельной проверки.</p>':`<div class="info-box"><strong>Обучение пока недоступно</strong><p>Проверенных примеров: ${result.expert_verified_records}. Кандидаты не являются GOLD.</p><ul>${result.missing.map(x=>`<li>${esc(x)}</li>`).join('')}</ul></div>`}finally{button.disabled=false}});
const previousOpenFinding=openFinding;
openFinding=function(id){previousOpenFinding(id);if(currentFinding?.expected_value&&currentFinding?.actual_value&&currentFinding.expected_value.normalized===currentFinding.actual_value.normalized&&currentFinding.expected_value.unit===currentFinding.actual_value.unit){$('#finding-detail').insertAdjacentHTML('afterbegin','<p class="value-match"><strong>Значения совпадают — это не расхождение.</strong></p>')}const equal=currentFinding?.expected_value?.normalized===currentFinding?.actual_value?.normalized&&currentFinding?.expected_value?.unit===currentFinding?.actual_value?.unit;$('#confirm').hidden=equal;$('#reject').textContent=equal?'Подтвердить совпадение':'Отклонить';$('#expert-verified').checked=false;$('#training-consent').checked=false};
$('#edit-evidence').onclick=()=>{
 if(!currentFinding)return;
 const original=currentFinding.current_evidence||currentFinding.evidence;
 openModal('Уточнить доказательства',`<p>Исправьте прочитанное значение и цитату. Чтобы изменить источник или область, сначала откройте документ в панели и выделите фрагмент. После правки решение вернётся в уточнение.</p>${original.map((e,i)=>{const d=documents.find(d=>d.id===e.file_id),p=panels[e.stage];return `<fieldset class="manual-fields"><legend>${e.role==='expected'?'Ожидаемое':'Фактическое'} · ${labels[e.stage]}</legend><p>${esc(d?.name)} · стр. ${e.page}</p><label>Прочитанное значение<input id="correct-value-${i}" value="${esc(e.value.raw)}" required></label><label>Цитата<textarea id="correct-quote-${i}" required>${esc(e.quote)}</textarea></label>${p?.draft?`<label class="check"><input type="checkbox" id="correct-region-${i}"> Использовать выделенную область в панели ${labels[e.stage]}, стр. ${p.page}</label>`:''}</fieldset>`}).join('')}<label>Основание исправления<input id="correct-reason" required minlength="3"></label><details><summary>Техническая карточка исходных доказательств</summary><pre>${esc(JSON.stringify(original,null,2))}</pre></details>`,async()=>{const evidence=original.map((e,i)=>{const drawn=$('#correct-region-'+i)?.checked,p=panels[e.stage];return {file_id:drawn?p.file:e.file_id,page:drawn?p.page:e.page,bbox:drawn?p.draft:e.bbox,role:e.role,raw:$('#correct-value-'+i).value,quote:$('#correct-quote-'+i).value}});await post('/findings/'+currentFinding.id+'/correction',{evidence,reason:$('#correct-reason').value});await loadObject();openFinding(currentFinding.id)},'Сохранить уточнение');
};

const previousInstallTools=installTools;
installTools=function(){previousInstallTools();$$('.viewer').forEach(el=>{if(el.querySelector('[data-dimensions]'))return;const b=document.createElement('button');b.type='button';b.textContent='Размеры';b.title='Найти подписи рядом с векторными линиями в выбранной области';b.dataset.dimensions='1';el.querySelector('.viewer-controls').append(b);b.hidden=!can('vision');b.onclick=()=>{const stage=el.dataset.stage,p=panels[stage];if(!p.file)return notice('Выберите документ',true);openModal('Размеры элемента',`<p>Выделите область инструментом «Область» перед запуском. Инспектор проверяет связь подписи с элементом и единицы по чертежу.</p><label>Параметр<select id="dimension-code"><option value="M-058">Толщина фундамента</option><option value="M-059">Толщина перекрытия</option><option value="M-060">Сечение колонны</option><option value="M-061">Толщина стены</option><option value="M-040">Ширина коридора</option><option value="M-041">Ширина двери</option><option value="M-030">Ширина проезда</option></select></label><label>Конкретный элемент / помещение<input id="dimension-entity" required></label><label>Единицы по примечанию чертежа<select id="dimension-unit"><option value="mm">мм</option><option value="m">м</option></select></label>`,async()=>{const result=await post('/documents/'+p.file+'/dimensions',{page:p.page,bbox:p.draft||p.evidence?.bbox||[0,0,1,1],entity:$('#dimension-entity').value,parameter_code:$('#dimension-code').value,unit:$('#dimension-unit').value});const foot=el.querySelector('.viewer-foot');foot.innerHTML=`<p>${esc(result.limitation)}</p><p>Найдено размерных подписей: ${result.dimensions.length}</p>`+result.dimensions.map((d,i)=>`<button type="button" data-dimension="${i}">${esc(d.source_text)} ${esc(result.unit_basis==='INSPECTOR_INPUT'?d.unit:'')}</button>`).join('');foot.querySelectorAll('[data-dimension]').forEach(x=>x.onclick=()=>{const d=result.dimensions[Number(x.dataset.dimension)];p.evidence={file_id:p.file,page:p.page,bbox:d.bbox,quote:d.source_text};renderPanel(stage)});},'Найти подписи')}})};

const previousLoadSystem=loadSystem;
loadSystem=async function(){await previousLoadSystem();if(!can('quality'))return;const q=await api('/quality'),m=q.current_development;if(!m?.models)return;const articles=Object.entries(m.models).map(([name,r])=>`<article><h3>${esc(name)}</h3><p>${r.n} фрагмента одного объекта. После проверки единиц и смысла: ${r.after_unit_normalization_positive_extraction}/${r.positive_cases} значений; корректных отказов: ${r.correct_abstentions_after_validation}/${r.negative_cases}.</p><p>Среднее время вызова со сменой модели: ${r.mean_seconds_including_model_switch} с.</p></article>`).join('');$('#system-info').innerHTML=`<strong>Качество автоматического этапа · разработческая проверка</strong><p>Это малый повторный тест, не независимая приёмка. Пороговые Precision / Recall / F1 и CER естественных сканов пока не установлены.</p><div class="quality-grid">${articles}</div><p>Экспертных локальных GOLD-примеров: ${m.independent_gold.expert_attested_local_records}. Подготовлена очередь настоящих источников для эксперта.</p><details><summary>Технические измерения и версии</summary><pre>${esc(JSON.stringify(m,null,2))}</pre></details>`};


// Main route: one object, one package, one background launch.
let coverageState=null,coverageObject=null;
function installSimpleJourney(){
 if(!$('#prepare-workflow')||$('#package-overview'))return;
 $('.workflow-guide').insertAdjacentHTML('beforebegin','<div class="journey-steps" aria-label="Порядок проверки">1. Объект → 2. Комплект → 3. Запуск → 4. Прогресс → 5. Результаты → 6. Протокол</div><section id="package-overview" class="info-box" aria-live="polite"></section>');
 $('.workflow-guide .workflow-actions').prepend($('#compare'));
 $('#compare').textContent='Запустить обработку и сверку';
 $('#compare').onclick=action(async()=>{
  const id=needObject(),supported=documents.filter(d=>d.parse_status!=='UNSUPPORTED').length;
  openModal('Проверить комплект объекта',`<p><strong>${esc(objectDetail.object.name)}</strong></p><p>Поддерживаемых файлов: ${supported} из ${documents.length}. Будут обработаны все страницы, включая OCR сканов. Затем модель проанализирует до трёх найденных страниц на параметр, и система выполнит предварительную сверку. Готовые страницы используются из кеша.</p><p>Выбор редакций сохраняется. Если основания актуальности отсутствуют, результат потребует уточнения. Извлечение текста не подтверждает отсутствие нарушений.</p>`,async()=>{if(id!==objectId)throw Error('Объект изменился. Откройте запуск заново.');await post('/objects/'+id+'/jobs',{kind:'workflow',all_documents:true,max_pages:0,file_limit:0,ocr:true,use_model:true,page_budget:396});$('#finding-filter').value='all';resultMode='findings';notice('Полный комплект поставлен в очередь. Результаты появятся по мере обработки.');await loadObject()},'Запустить');
 });
 $('#prepare-workflow').textContent='Выбрать редакции из панелей';
 $('#prepare-workflow').className='secondary';
 $('#advanced-processing .workflow-actions').append($('#prepare-workflow'),$('#manual-open'));
 $('#advanced-processing summary').textContent='Выбор редакций и ручная проверка';
 $('#parse-open').hidden=true;$('#assisted').hidden=true;
 $('nav [data-tab=documents] span:last-child').textContent='Комплект документов';
 $('nav [data-tab=review] span:last-child').textContent='Результаты проверки';
 const guide=$('#tab-help ol');if(guide)guide.innerHTML='<li>Выберите объект. Документы школы и детского сада по одному адресу не объединяются.</li><li>Откройте комплект: проверьте стадии, редакции и основания утверждения. Отсутствие реквизитов требует уточнения.</li><li>Нажмите «Запустить обработку и сверку». OCR, извлечение моделью и сравнение выполняются в очереди.</li><li>Следите за числом обработанных файлов, ошибками и пропусками. Результаты обновляются автоматически.</li><li>Откройте карточку или найденное значение: источник и область подсветятся. Совпадение не является нарушением.</li><li>Примите решение по доказательствам, затем откройте протокол, экспортируйте и при необходимости отправьте финальную версию в тестовый РиН.</li>';
}
function renderCoverage(){
 const box=$('#package-overview');if(!box)return;
 if(coverageObject!==objectId||!coverageState){box.textContent='Загрузка состава выбранного объекта…';return}
 const c=coverageState.documents;
 box.innerHTML=`<strong>Комплект: ${c.total} файлов</strong><div class="coverage-counts"><span>Обработано полностью: <b>${c.processed}</b></span><span>Ошибки файлов: <b>${c.errors}</b></span><span>Пропущено: <b>${c.skipped}</b></span><span>Частично: <b>${c.partial}</b></span><span>Ожидают / обрабатываются: <b>${c.remaining}</b></span></div><p>Извлечены все страницы у ${(100*c.coverage).toFixed(1)}% поддерживаемых файлов. Это покрытие обработки, не точность проверки.</p><button id="coverage-files">Причины ошибок и пропусков</button>`;
 $('#coverage-files').onclick=()=>{const rows=coverageState.files.filter(d=>d.parse_status!=='PARSED');openModal('Состояние файлов',`<p>Всего в этом списке: ${rows.length}. Все файлы доступны в реестре.</p><input id="coverage-filter" placeholder="Поиск файла"><div id="coverage-file-list"></div>`,async()=>{},'Закрыть');const draw=()=>{const q=$('#coverage-filter').value.toLowerCase(),filtered=rows.filter(d=>(d.name+' '+d.reason).toLowerCase().includes(q));$('#coverage-file-list').innerHTML=`<p>Найдено: ${filtered.length}; показаны первые 100</p>`+filtered.slice(0,100).map(d=>`<article><strong>${esc(d.name)}</strong> · ${labels[d.stage]}<p>${esc(d.reason)}</p></article>`).join('')};$('#coverage-filter').oninput=draw;draw()};
}
const coverageRoleUI=roleUI;
roleUI=function(){coverageRoleUI();installSimpleJourney();$('#parse-open').hidden=true;$('#assisted').hidden=true;$$('[data-extract]').forEach(b=>b.hidden=true)};
const coverageLoadObject=loadObject;
loadObject=async function(){const id=objectId;await coverageLoadObject();if(id!==objectId||objectDetail?.object.id!==id)return;const state=await api('/objects/'+id+'/coverage');if(id!==objectId)return;coverageState=state;coverageObject=id;renderCoverage();renderMatrix();if(currentTab==='system')renderObjectQuality(id,state);$('#compare').disabled=objectDetail.object.status==='FINALIZED'||state.jobs.some(j=>['RUNNING','QUEUED'].includes(j.status));};
const coverageChange=$('#object-select').onchange;
$('#object-select').onchange=async()=>{coverageState=null;coverageObject=null;if($('#object-quality'))$('#object-quality').textContent='Загрузка показателей выбранного объекта…';['#doc-query','#doc-stage','#doc-category','#matrix-query','#matrix-status'].forEach(s=>{if($(s))$(s).value=''});if($('#doc-sort'))$('#doc-sort').value='name';renderCoverage();if($('#modal').open)$('#modal').close();await coverageChange()};

const journeyUpdateJobs=updateJobs;
updateJobs=function(jobs){
 journeyUpdateJobs(jobs);
 const own=jobs.filter(j=>j.object_id===objectId);
 activeJob=own.find(j=>['RUNNING','QUEUED'].includes(j.status))||own[0]||null;
 $('#progress-box').hidden=!activeJob;
 if(!activeJob)return;
 const finished=['DONE','DONE_WITH_ERRORS'].includes(activeJob.status);
 $('#progress-text').textContent=(labels[activeJob.status]||activeJob.status)+' · '+(activeJob.message||'Обработка комплекта');
 $('#progress').max=finished?1:(activeJob.total||100);$('#progress').value=finished?1:(activeJob.progress||0);
 $('#pause').hidden=!can('prepare')||activeJob.status==='DONE'||objectDetail?.object.status==='FINALIZED';
 $('#pause').textContent=['PAUSED','ERROR','DONE_WITH_ERRORS'].includes(activeJob.status)?'Продолжить обработку':'Приостановить';
};
$('#pause').onclick=action(async()=>{
 if(!activeJob)return;
 const id=activeJob.id,resuming=['PAUSED','ERROR','DONE_WITH_ERRORS'].includes(activeJob.status);
 await post('/jobs/'+id+(resuming?'/resume':'/pause'));
 notice(resuming?'Обработка продолжится с сохранённого места.':'Приостановка после текущей страницы или запроса модели. Результаты сохранятся.');
 updateJobs(await api('/jobs?object_id='+encodeURIComponent(objectId)));await loadObject();
});

const coverageRenderMatrix=renderMatrix;
renderMatrix=function(){coverageRenderMatrix();if(coverageObject!==objectId||!coverageState)return;$$('#matrix-rows tr').forEach(row=>{const code=row.cells[0]?.querySelector('strong')?.textContent,p=coverageState.parameters.find(p=>p.code===code);if(p)row.cells[2].innerHTML=`<strong>${esc(p.reason_label)}</strong><small>${p.literal_extraction==='anchored'?'Есть извлечение текстовыми правилами':'Есть извлечение моделью с проверкой цитаты'}. Это не подтверждение полной реализации графического правила.</small>`})};

const priorObjectQuality=loadSystem;
loadSystem=async function(){
 const id=objectId;
 if(!$('#object-quality')){const box=document.createElement('div');box.id='object-quality';box.className='info-box';$('#system-info').before(box);const older=document.createElement('details');older.innerHTML='<summary>Другие разработческие прогоны и технические сведения</summary>';$('#system-info').before(older);older.append($('#system-info'))}
 await priorObjectQuality();
 if(!id||id!==objectId||!can('quality'))return;
 const state=await api('/objects/'+id+'/coverage');
 if(id!==objectId)return;
 renderObjectQuality(id,state);
};
function renderObjectQuality(id,state){
 if(id!==objectId||objectDetail?.object.id!==id||!$('#object-quality'))return;
 const c=state.documents;
 const totalPages=state.pages.reduce((n,p)=>n+Number(p.n),0),lowPages=state.pages.filter(p=>p.quality!=='GOOD').reduce((n,p)=>n+Number(p.n),0);
 $('#object-quality').innerHTML=`<section data-object-quality="${esc(id)}"><h3>${esc(objectDetail.object.name)} · текущая обработка</h3><p>Всего файлов: ${c.total}. Полностью извлечено ${c.processed} из ${c.supported} поддерживаемых файлов (${(100*c.coverage).toFixed(1)}%). Ошибки: ${c.errors}; пропуски: ${c.skipped}; частично: ${c.partial}; ожидают или выполняются: ${c.remaining}.</p><p>Прочитано страниц: ${totalPages}. Требуют проверки качества: ${lowPages}. Уверенность распознавания не является точностью.</p><p>Параметров с сопоставимыми доказательствами: ${state.parameter_counts.COMPARED||0} из ${state.parameters.length}. Подробные причины доступны в матрице.</p><p><strong>OCR CER, Precision, Recall и F1 не установлены:</strong> ${esc(state.quality_metrics.reason)}.</p></section>`;
};

const sourceFragmentHighlight=fragmentHighlight;
fragmentHighlight=function(evidence){
 const fragment=sourceFragmentHighlight(evidence);
 if(fragment)return fragment;
 return evidence?.demo?[.01,.01,.99,.99]:null;
};
const sourceRenderPanel=renderPanel;
renderPanel=function(stage){
 sourceRenderPanel(stage);
 const panel=panels[stage],el=$(`.viewer[data-stage=${stage}]`);
 if(panel.evidence?.demo&&panel.evidence.fragment_found===false){
  el?.querySelector('.evidence-box')?.classList.add('page-evidence');
 }
};
