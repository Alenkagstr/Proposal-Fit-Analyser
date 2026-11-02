const $=id=>document.getElementById(id);
let result=null,jobId=null,busy=false,aiReady=false;
let pendingPayload=null,resumeJob=false;
const uploadedLinks={};
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
function count(id){$(id+'-count').textContent=$(id).value.length.toLocaleString('ru')+' символов';}
for(const id of ['cv','proposal','application']) $(id).addEventListener('input',()=>count(id));
async function upload(file,target,label){
 if(!file)return;
 error(''); if(file.size>8*1024*1024){error('Максимальный размер файла — 8 МБ.');return;}
 const title=label.querySelector('strong');title.textContent='Читаем документ…';$('run').disabled=true;
 try{const form=new FormData();form.append('file',file);const r=await fetch('/api/extract',{method:'POST',body:form});const data=await r.json();if(!r.ok)throw Error(data.detail||'Не удалось загрузить файл.');$(target).value=data.text;count(target);uploadedLinks[target]=data.links||[];showInputLinks();title.textContent='✓ '+file.name;}catch(e){title.textContent='Выберите файл или перетащите сюда';error(e.message);}finally{$('run').disabled=false;}
}
for(const input of document.querySelectorAll('input[type=file]')){
 const label=input.closest('label');input.addEventListener('change',()=>upload(input.files[0],input.dataset.target,label));
 label.addEventListener('dragover',e=>{e.preventDefault();label.classList.add('drag');});label.addEventListener('dragleave',()=>label.classList.remove('drag'));
 label.addEventListener('drop',e=>{e.preventDefault();label.classList.remove('drag');upload(e.dataTransfer.files[0],input.dataset.target,label);});
}
$('demo').onclick=()=>{
 $('cv').value='Анна Петрова — исследователь в области машинного обучения.\n2022–2024: проект по анализу медицинских изображений. Разработала модели компьютерного зрения на Python и PyTorch.\nПубликация: «Сегментация медицинских изображений с помощью глубокого обучения», 2024.\nОпыт подготовки датасетов и оценки качества моделей.\nНавыки: Python, PyTorch, статистический анализ. Доступ к вычислительной инфраструктуре не указан.';
 $('proposal').value='Цель: разработать методы машинного обучения для анализа медицинских изображений.\nМетод: обучить модели компьютерного зрения на Python и PyTorch.\nЗадача: провести статистический анализ и оценку качества моделей.\nРесурс: необходим доступ к GPU-кластеру и лаборатории для клинической валидации.\nЭтап: за 12 месяцев собрать датасет и проверить модель на независимой выборке.\nРезультат: подготовить публикацию и открытый датасет.';
 $('candidate').value='Анна Петрова · учебный пример';$('mode').value='local';$('extra').value='';$('profiles').value='';$('application').value='';count('application');count('cv');count('proposal');$('mode').dispatchEvent(new Event('change'));error('');
};
function showInputLinks(){
 const links=uploadedLinks.application||[];
 $('input-links').textContent=links.length?`В загруженной заявке найдено ссылок: ${links.length}. Все ссылки появятся в результатах анализа.`:'';
}
$('application').addEventListener('input',()=>{$('input-links').textContent='Ссылки из текущего текста будут извлечены при анализе.';});
$('mode').onchange=()=>{
 $('mode-note').textContent=$('mode').value==='ai'?'Требования → опыт CV и заявки → сопоставление → справка. Тексты отправляются Neural Deep.':'Поиск текстовых совпадений без лимита в 100 пунктов. Компетенции проверяет эксперт.';
 if($('mode').value==='ai'&&!aiReady){$('ai-settings').open=true;$('ai-message').textContent='Сначала подключите ключ и проверьте выбранную модель. Анализ не будет подменён локальным режимом.';}
};
function selectModel(model){
 const select=$('ai-model');
 if(model&&!Array.from(select.options).some(option=>option.value===model))select.add(new Option(model,model));
 select.value=model||'qwen3.6-35b-a3b';
}
fetch('/api/health').then(r=>r.json()).then(d=>{
 aiReady=d.ai_available;
 selectModel(d.model);
 $('ai-status').textContent=aiReady?'Ключ настроен · '+d.model:'Ключ не настроен';
 if(aiReady){$('ai-option').textContent='AI-анализ · '+d.model;$('mode').value='ai';$('mode').dispatchEvent(new Event('change'));}
 else $('ai-settings').open=true;
}).catch(()=>error('Сервер недоступен. Проверьте, что приложение запущено.'));
async function aiAction(path,payload){
 const checking=path==='/api/ai/check',started=Date.now();
 const showWaiting=()=>{$('ai-message').textContent=checking?`Проверяем ответ Neural Deep… ${Math.floor((Date.now()-started)/1000)} с. Лимит проверки — 30 с.`:'Сохраняем ключ…';};
 showWaiting();const timer=setInterval(showWaiting,1000);
 $('ai-connect').disabled=$('ai-check').disabled=true;
 try{
  const {response,data}=await boundedFetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)},checking?45000:15000);if(!response.ok)throw Error(data.detail||'Не удалось проверить AI.');
  $('ai-message').textContent=data.message;return data;
 }catch(e){$('ai-message').textContent=e.message;return null;}
 finally{clearInterval(timer);$('ai-key').value='';$('ai-connect').disabled=$('ai-check').disabled=false;}
}
$('ai-connect').onclick=async()=>{
 const data=await aiAction('/api/ai/connect',{api_key:$('ai-key').value});if(!data)return;
 aiReady=false;
 selectModel(data.model);
 $('ai-status').textContent='Ключ принят · проверьте модель';
};
$('ai-check').onclick=async()=>{
 const data=await aiAction('/api/ai/check',{model:$('ai-model').value});if(!data)return;
 aiReady=true;$('ai-status').textContent='AI проверен · '+data.model;$('ai-option').textContent='AI-анализ · '+data.model;
 $('mode').value='ai';$('mode').dispatchEvent(new Event('change'));error('');
};
async function runAnalysis(retry=false){
 if(busy)return;
 if(!retry){
  if($('mode').value==='ai'&&!aiReady){$('ai-settings').open=true;error('Подключите Neural Deep и нажмите «Проверить AI».');return;}
  pendingPayload={};for(const k of ['cv','proposal','application','extra','candidate','profiles','mode'])pendingPayload[k]=$(k).value;
  resumeJob=false;
 }
 busy=true;$('run').disabled=true;view('progress');
 $('progress-failure').hidden=true;$('progress-spinner').hidden=false;$('progress-note').hidden=false;
 $('progress-title').textContent='Соединяем факты';$('stage').hidden=false;
 if(!resumeJob)$('stage').textContent='Подготовка материалов';
 const deadline=Date.now()+630000;
 try{
  if(!resumeJob){
   const {response,data}=await boundedFetch('/api/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(pendingPayload)});
   if(!response.ok)throw Error(data.detail||'Ошибка анализа.');jobId=data.id;resumeJob=true;
  }
  while(Date.now()<deadline){
   const {response:r,data:j}=await boundedFetch('/api/jobs/'+jobId,{},Math.min(15000,deadline-Date.now()));
   if(!r.ok){if(r.status===404)resumeJob=false;throw Error(j.detail||'Результат недоступен.');}
   $('stage').textContent=j.failed_stage||j.stage;
   if(j.status==='error'){resumeJob=false;throw Error(j.error);}
   if(j.status==='done'){resumeJob=false;result=j.result;render();view('result');return;}
   await new Promise(resolve=>setTimeout(resolve,1200));
  }
  throw Error('Превышено время ожидания анализа. Повтор проверит состояние уже запущенного анализа.');
 }catch(e){
  $('progress-spinner').hidden=true;$('progress-note').hidden=true;$('progress-title').textContent='Анализ не завершён';
  $('stage').hidden=true;
  $('progress-error').textContent=/тайм-аут.*Neural Deep|Neural Deep.*не ответил/i.test(e.message)?'Neural Deep не ответил за отведённое время запроса.':e.message;
  $('progress-failure').hidden=false;
 }finally{busy=false;$('run').disabled=false;}
}
$('analysis-form').onsubmit=e=>{e.preventDefault();runAnalysis();};
$('retry-analysis').onclick=()=>runAnalysis(true);
$('progress-back').onclick=()=>view('input');

function matrix(){const wanted=$('filter').value;const rows=result.matrix.filter(m=>wanted==='all'||m.status===wanted);$('matrix-body').innerHTML=rows.length?rows.map(m=>`<tr><td><small>${esc(m.req_id)} · ${esc(m.type)} · ${esc(m.importance)} · ${esc(m.stated)}</small><strong>${esc(m.requirement)}</strong><div class="source-quote">«${esc(m.quote)}»</div></td><td><span class="status s${statuses.indexOf(m.status)}">${esc(m.status)}</span></td><td>${esc(m.rationale)}${m.auto_check?`<p class="hint">Проверка: ${esc(m.auto_check)}</p>`:''}${m.evidence_ids.length?`<details><summary>Доказательства · ${m.evidence_ids.length}</summary>${m.evidence_ids.map(id=>{const ev=result.evidence.find(x=>x.id===id);return ev?`<blockquote><strong>${esc(ev.id)} · ${esc(ev.source)} · ${esc(ev.strength)}</strong><p>«${esc(ev.quote)}»</p><small>${ev.quote_verified?'Цитата найдена в документе':'Цитату необходимо проверить'}</small>${ev.context?`<details><summary>Исходный фрагмент · ${esc(ev.fragment_id)} · символы ${ev.source_start+1}–${ev.source_end}</summary><p>${esc(ev.context)}</p></details>`:''}</blockquote>`:'';}).join('')}</details>`:'<p class="hint">Подтверждение в материалах не найдено</p>'}</td></tr>`).join(''):'<tr><td colspan="3">Нет требований с выбранным статусом.</td></tr>';}
function render(){
 $('nav-result').disabled=false;$('result-candidate').textContent=result.candidate+' · '+result.matrix.length+' требований';
 $('analysis-notice').textContent=result.mode==='local'?'Предварительный режим без AI: показаны совпадения слов. Все выводы требуют экспертной проверки; отсутствие совпадений не означает отсутствие опыта.':'Гибридный поиск: многоязычные эмбеддинги и научные термины → оценка контекста моделью → проверка цитат. Сходство текстов само по себе не подтверждает опыт.';
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
$('delete').onclick=async()=>{try{const r=await fetch('/api/jobs/'+jobId,{method:'DELETE'});if(!r.ok)throw Error('Не удалось удалить анализ.');result=null;jobId=null;$('nav-result').disabled=true;for(const id of ['cv','proposal','application','extra','candidate','profiles'])$(id).value='';for(const id of ['cv','proposal','application']){count(id);$(id+'-file').value='';$(id+'-file').closest('label').querySelector('strong').textContent='Выберите файл или перетащите сюда';}uploadedLinks.application=[];showInputLinks();for(const id of ['matrix-body','risks-panel','report-text','sources-panel','stats'])$(id).textContent='';view('input');}catch(e){error(e.message);}};
