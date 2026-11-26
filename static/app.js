const $=id=>document.getElementById(id);
let result=null,jobId=null,busy=false,aiReady=false;
let pendingPayload=null,pendingJobId=null,resumeJob=false;
let decision='pending',historyEntries=[],historyRequest=0,openingHistory=false,savingDecision=false;
async function boundedFetch(url,options={},timeout=15000){
 const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),timeout);
 try{const response=await fetch(url,{...options,signal:controller.signal});const data=await response.json();return {response,data};}
 catch(e){if(e.name==='AbortError')throw Error('Сервер не ответил вовремя. Проверьте соединение и повторите.');throw e;}
 finally{clearTimeout(timer);}
}
const statuses=['подтверждено','частично подтверждено','не найдено в материалах','требует экспертной проверки'];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function error(message){$('error').textContent=message;$('error').hidden=!message;}
function view(name){for(const n of ['input','progress','result']) $(n+'-view').hidden=n!==name;$('crumb').textContent=name==='input'?'Новый анализ':name==='progress'?'Сопоставление':'Результаты';$('nav-input').classList.toggle('active',name==='input');$('nav-result').classList.toggle('active',name==='result');error('');window.scrollTo({top:0,behavior:'smooth'});}
async function upload(file,target,label){
 if(!file)return;
 error(''); if(file.size>8*1024*1024){error('Максимальный размер файла — 8 МБ.');return;}
 const title=$(target+'-status');title.textContent='Читаем документ…';$('run').disabled=true;
 try{const form=new FormData();form.append('file',file);const r=await fetch('/api/extract',{method:'POST',body:form});const data=await r.json();if(!r.ok)throw Error(data.detail||'Не удалось загрузить файл.');$(target).value=data.text;title.textContent='✓ '+file.name;label.classList.add('loaded');}catch(e){title.textContent=$(target).value?'Предыдущий документ сохранён':'Файл не загружен';error(e.message);}finally{$('run').disabled=false;}
}
for(const input of document.querySelectorAll('input[type=file]')){
 const label=input.closest('label');input.addEventListener('change',()=>upload(input.files[0],input.dataset.target,label));
 label.addEventListener('dragover',e=>{e.preventDefault();label.classList.add('drag');});label.addEventListener('dragleave',()=>label.classList.remove('drag'));
 label.addEventListener('drop',e=>{e.preventDefault();label.classList.remove('drag');upload(e.dataTransfer.files[0],input.dataset.target,label);});
}
$('demo').onclick=()=>{
 $('cv').value='Анна Петрова — исследователь в области машинного обучения.\n2022–2024: проект по анализу медицинских изображений. Разработала модели компьютерного зрения на Python и PyTorch.\nПубликация: «Сегментация медицинских изображений с помощью глубокого обучения», 2024.\nОпыт подготовки датасетов и оценки качества моделей.\nНавыки: Python, PyTorch, статистический анализ. Доступ к вычислительной инфраструктуре не указан.';
 $('proposal').value='Цель: разработать методы машинного обучения для анализа медицинских изображений.\nМетод: обучить модели компьютерного зрения на Python и PyTorch.\nЗадача: провести статистический анализ и оценку качества моделей.\nРесурс: необходим доступ к GPU-кластеру и лаборатории для клинической валидации.\nЭтап: за 12 месяцев собрать датасет и проверить модель на независимой выборке.\nРезультат: подготовить публикацию и открытый датасет.';
 for(const id of ['cv','application','proposal']){$(id+'-file').value='';$(id+'-file').closest('label').classList.toggle('loaded',id!=='application');$(id+'-status').textContent=id==='application'?'Необязательно':'Учебный пример';}
 $('candidate').value='Анна Петрова · учебный пример';$('mode').value='local';$('extra').value='';$('profiles').value='';$('application').value='';$('mode').dispatchEvent(new Event('change'));error('');
};
$('mode').onchange=()=>{
 $('mode-note').hidden=$('mode').value==='ai';
 if($('mode').value==='ai'&&!aiReady){$('ai-settings').open=true;$('ai-message').textContent='Задайте ключ в .env, перезапустите сервер и проверьте выбранную модель. Анализ не будет подменён локальным режимом.';}
};
function selectModel(model){
 const select=$('ai-model');
 if(model&&!Array.from(select.options).some(option=>option.value===model))select.add(new Option(model,model));
 select.value=model||'qwen3.6-35b-a3b';
}
fetch('/api/health').then(r=>r.json()).then(d=>{
 aiReady=d.ai_available;
 selectModel(d.model);
 if(aiReady){$('ai-option').textContent='AI-анализ · '+d.model;$('mode').value='ai';$('mode').dispatchEvent(new Event('change'));}
 else $('ai-settings').open=true;
}).catch(()=>error('Сервер недоступен. Проверьте, что приложение запущено.'));
async function aiAction(path,payload){
 const checking=path==='/api/ai/check',started=Date.now();
 const showWaiting=()=>{$('ai-message').textContent=checking?`Проверяем ответ Neural Deep… ${Math.floor((Date.now()-started)/1000)} с. Лимит проверки — 30 с.`:'Сохраняем ключ…';};
 showWaiting();const timer=setInterval(showWaiting,1000);
 $('ai-check').disabled=true;
 try{
  const {response,data}=await boundedFetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},checking?45000:15000);if(!response.ok)throw Error(data.detail||'Не удалось проверить AI.');
  $('ai-message').textContent=data.message;return data;
 }catch(e){$('ai-message').textContent=e.message;return null;}
 finally{clearInterval(timer);$('ai-check').disabled=false;}
}
$('ai-check').onclick=async()=>{
 const data=await aiAction('/api/ai/check',{model:$('ai-model').value});if(!data)return;
 aiReady=true;$('ai-option').textContent='AI-анализ · '+data.model;
 $('mode').value='ai';$('mode').dispatchEvent(new Event('change'));error('');
};
async function runAnalysis(retry=false){
 if(busy||openingHistory||savingDecision)return;
 if(!retry){
  if($('mode').value==='ai'&&!aiReady){$('ai-settings').open=true;error('Подключите Neural Deep и нажмите «Проверить AI».');return;}
  if(!$('cv').value.trim()||!$('proposal').value.trim()){error('Загрузите CV и Proposal перед анализом.');return;}
  pendingPayload={};for(const k of ['cv','proposal','application','extra','candidate','profiles','mode'])pendingPayload[k]=$(k).value;
  if(!pendingPayload.candidate.trim())pendingPayload.candidate=$('cv-file').files[0]?.name.slice(0,150)||'';
  resumeJob=false;
 }
 busy=true;$('run').disabled=true;view('progress');
 $('progress-failure').hidden=true;$('progress-spinner').hidden=false;
 $('progress-title').textContent='Соединяем факты';$('stage').hidden=false;
 if(!resumeJob)$('stage').textContent='Подготовка материалов';
 let failedStage='';
 const deadline=Date.now()+630000;
 try{
  if(!resumeJob){
   const {response,data}=await boundedFetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pendingPayload)});
   if(!response.ok)throw Error(data.detail||'Ошибка анализа.');pendingJobId=data.id;resumeJob=true;
  }
  while(Date.now()<deadline){
   const {response:r,data:j}=await boundedFetch('/api/jobs/'+pendingJobId,{},Math.min(15000,deadline-Date.now()));
   if(!r.ok){if(r.status===404)resumeJob=false;throw Error(j.detail||'Результат недоступен.');}
   if(j.stage)$('stage').textContent=j.failed_stage||j.stage;
   if(j.status==='error'){resumeJob=false;failedStage=j.failed_stage||j.stage||'';throw Error(j.error);}
   if(j.status==='done'){resumeJob=false;jobId=pendingJobId;result=j.result;decision=j.decision||'pending';render();view('result');refreshHistory();return;}
   await new Promise(resolve=>setTimeout(resolve,1200));
  }
  throw Error('Превышено время ожидания анализа. Повтор проверит состояние уже запущенного анализа.');
 }catch(e){
  $('progress-spinner').hidden=true;$('progress-title').textContent='Анализ не завершён';
  $('stage').hidden=true;
  $('progress-error').textContent=failedStage?`Этап «${failedStage}». ${e.message}`:e.message;
  $('progress-failure').hidden=false;
 }finally{busy=false;$('run').disabled=false;}
}
$('analysis-form').onsubmit=e=>{e.preventDefault();runAnalysis();};
$('retry-analysis').onclick=()=>runAnalysis(true);
$('progress-back').onclick=()=>view('input');

function matrix(){const wanted=$('filter').value;const rows=result.matrix.filter(m=>wanted==='all'||m.status===wanted);$('matrix-body').innerHTML=rows.length?rows.map(m=>`<tr><td><small>${esc(m.req_id)} · ${esc(m.type)} · ${esc(m.importance)} · ${esc(m.stated)}</small><strong>${esc(m.requirement)}</strong><div class="source-quote">«${esc(m.quote)}»</div></td><td><span class="status s${statuses.indexOf(m.status)}">${esc(m.status)}</span></td><td>${esc(m.rationale)}${m.status===statuses[1]?`<p><strong>Что подтверждено:</strong> ${esc(m.confirmed_part||m.rationale)}</p><p><strong>Чего не хватает:</strong> ${esc(m.missing_part||m.question)}</p>`:''}${m.auto_check?`<p class="hint">Проверка: ${esc(m.auto_check)}</p>`:''}${m.evidence_ids.length?`<details><summary>Доказательства · ${m.evidence_ids.length}</summary>${m.evidence_ids.map(id=>{const ev=result.evidence.find(x=>x.id===id);return ev?`<blockquote><strong>${esc(ev.id)} · ${esc(ev.source)} · ${esc(ev.strength)}</strong><p>«${esc(ev.quote)}»</p><small>${ev.quote_verified?'Цитата найдена в документе':'Цитату необходимо проверить'}</small>${ev.context?`<details><summary>Исходный фрагмент · ${esc(ev.fragment_id)} · символы ${ev.source_start+1}–${ev.source_end}</summary><p>${esc(ev.context)}</p></details>`:''}</blockquote>`:'';}).join('')}</details>`:'<p class="hint">Подтверждение в материалах не найдено</p>'}</td></tr>`).join(''):'<tr><td colspan="3">Нет требований с выбранным статусом.</td></tr>';}
function render(){
 const recommendation=result.recommendation;
 $('recommendation').textContent=recommendation?'Рекомендация системы · '+decisionLabels[recommendation.decision]:'Рекомендация системы · Рассмотреть';
 $('recommendation-reason').textContent=recommendation?.reason||'Для этой записи рекомендация не была рассчитана.';
 $('candidate-decision').value=decision;
 $('decision-message').textContent='';
 drawHistory();
 $('nav-result').disabled=false;$('result-candidate').textContent=result.candidate+' · '+result.matrix.length+' требований';
 $('analysis-notice').hidden=result.mode!=='local';
 $('analysis-notice').textContent=result.mode==='local'?'Предварительный режим без AI: показаны совпадения слов. Все выводы требуют экспертной проверки; отсутствие совпадений не означает отсутствие опыта.':'';
 $('stats').innerHTML=statuses.map(s=>`<div class="stat"><i></i><strong>${result.matrix.filter(m=>m.status===s).length}</strong><small>${esc(s)}</small></div>`).join('');
 $('filter').value='all';matrix();
 $('risks-panel').innerHTML=result.risks.risks.map(r=>`<article class="risk-card"><span class="status s2">${esc(r.level)} риск</span><h3>${esc(r.risk)}</h3><p>Основание: ${esc(r.basis)}</p></article>`).join('')+`<div class="questions"><h3>Вопросы для уточнения</h3><ol>${result.risks.questions.map(q=>`<li>${esc(q)}</li>`).join('')}</ol></div>`;
 $('report-text').textContent=result.report;
 $('sources-panel').innerHTML='<p class="hint">Ссылки извлечены из предоставленных документов. Содержимое страниц не загружалось и не использовалось как доказательство.</p>'+(result.source_links||[]).map(x=>`<div class="link-item"><strong>${esc(x.type)}</strong><br><a href="${esc(x.url)}" target="_blank" rel="noopener noreferrer">${esc(x.url)}</a><small>Источник: ${esc(x.sources.join(', '))} · ${esc(x.status)}</small></div>`).join('');
 if(!result.source_links?.length)$('sources-panel').innerHTML+='<p>Ссылки в материалах не найдены.</p>';
 for(const fmt of ['xlsx','md','json']){$('export-'+fmt).href=`/api/jobs/${jobId}/export/${fmt}`;$('export-'+fmt).setAttribute('download','proposal-fit-report.'+fmt);}
 tab('matrix');
}
function tab(name){for(const n of ['matrix','risks','report','sources'])$(n+'-panel').hidden=n!==name;for(const b of document.querySelectorAll('[data-tab]')){b.classList.toggle('selected',b.dataset.tab===name);b.setAttribute('aria-selected',String(b.dataset.tab===name));} $('filter').hidden=name!=='matrix';}
for(const b of document.querySelectorAll('[data-tab]'))b.onclick=()=>tab(b.dataset.tab);
$('filter').onchange=matrix;$('back').onclick=()=>view('input');$('nav-input').onclick=()=>{if(!busy)view('input');};$('nav-result').onclick=()=>{if(result&&!busy)view('result');};
$('delete').onclick=async()=>{if(busy||openingHistory||savingDecision)return;openingHistory=true;try{const r=await fetch('/api/jobs/'+jobId,{method:'DELETE'});if(!r.ok)throw Error('Не удалось удалить анализ.');result=null;jobId=null;$('nav-result').disabled=true;for(const id of ['cv','proposal','application','extra','candidate','profiles'])$(id).value='';for(const id of ['cv','proposal','application']){$(id+'-file').value='';$(id+'-file').closest('label').classList.remove('loaded');$(id+'-status').textContent=id==='application'?'Необязательно':'Обязательный документ';}for(const id of ['matrix-body','risks-panel','report-text','sources-panel','stats'])$(id).textContent='';view('input');refreshHistory();}catch(e){error(e.message);}finally{openingHistory=false;}};

const decisionLabels={pending:'Без решения',shortlisted:'Оставить',review:'Рассмотреть',rejected:'Отклонить'};
const historyDate=new Intl.DateTimeFormat('ru',{dateStyle:'medium',timeStyle:'short'});

function drawHistory(){
 const filter=$('history-filter').value;
 const entries=historyEntries.filter(entry=>filter==='all'||entry.decision===filter);
 const list=$('history-list');
 list.replaceChildren();
 for(const entry of entries){
  const item=document.createElement('li');
  const button=document.createElement('button');
  button.type='button';
  if(jobId===entry.id)button.setAttribute('aria-current','true');
  const title=document.createElement('strong');
  title.textContent=entry.candidate;
  const details=document.createElement('small');
  details.textContent=historyDate.format(entry.created*1000)+' · '+decisionLabels[entry.decision];
  button.append(title,details);
  button.onclick=()=>openHistory(entry.id);
  item.append(button);
  list.append(item);
 }
 $('history-message').textContent=entries.length?'':filter==='all'?'Завершённых анализов пока нет.':'В этой категории пока нет анализов.';
}

async function refreshHistory(){
 const request=++historyRequest;
 try{
  const {response,data}=await boundedFetch('/api/history');
  if(!response.ok)throw Error('Не удалось загрузить историю.');
  if(request!==historyRequest)return;
  historyEntries=data;
  drawHistory();
 }catch(e){
  if(request===historyRequest)$('history-message').textContent='Не удалось загрузить историю.';
 }
}

async function openHistory(id){
 if(busy||openingHistory||savingDecision)return;
 openingHistory=true;
 try{
  const {response,data}=await boundedFetch('/api/jobs/'+id);
  if(!response.ok||!data.result)throw Error(data.detail||'Не удалось открыть анализ.');
  jobId=id;
  result=data.result;
  decision=data.decision||'pending';
  render();
  view('result');
 }catch(e){error(e.message);}
 finally{openingHistory=false;}
}

$('history-filter').onchange=drawHistory;
$('candidate-decision').onchange=async ()=>{
 if(!jobId||busy||openingHistory||savingDecision){
  $('candidate-decision').value=decision;
  return;
 }
 savingDecision=true;
 const field=$('candidate-decision');
 field.disabled=true;
 try{
  const {response,data}=await boundedFetch('/api/history/'+jobId,{
   method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify({decision:field.value})
  });
  if(!response.ok)throw Error(data.detail||'Не удалось сохранить решение.');
  decision=data.decision;
  $('decision-message').textContent='Решение сохранено.';
  refreshHistory();
 }catch(e){
  field.value=decision;
  $('decision-message').textContent=e.message;
 }finally{
  field.disabled=false;
  savingDecision=false;
 }
};
if(matchMedia('(max-width: 900px)').matches)$('history-menu').open=false;
refreshHistory();
