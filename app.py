"""Local Proposal Fit Analyser. Documents stay in memory except temporary parsing files."""
from __future__ import annotations
import io, json, os, re, tempfile, time, uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
import notebook_core as core

ROOT = Path(__file__).parent
app = FastAPI(title='Proposal Fit Analyser')
app.mount('/static', StaticFiles(directory=ROOT/'static'), name='static')
jobs = {}
lock = Lock()
pool = ThreadPoolExecutor(max_workers=1)
LIMIT = 8 * 1024 * 1024
STATUSES = core.STATUSES

from ai_service import CheckedLLM, AIError, safe_error, make_client, DEFAULT_MODEL
from documents import link_inventory, read_xlsx
from quote_checks import locate_quote
from retrieval import HybridRetriever, MODEL as EMBEDDING_MODEL
from retrieval_match import match_retrieved


def tokens(text):
    stop = {'this','with','from','that','will','have','для','при','или','как','что','это','его','the','and','our','project','проект','необходимо','требуется'}
    return {w for w in re.findall(r'[a-zа-яё][a-zа-яё0-9+-]{2,}', text.lower()) if w not in stop}


def preliminary(proposal, texts):
    """Conservative literal overlap, never confirms a competence."""
    sentences = [s.strip(' •-\t') for s in re.split(r'\n+|(?<=[.!?])\s+', proposal) if len(s.strip()) > 15]
    reqs = []
    for i, s in enumerate(sentences, 1):
        kind = 'задача'
        for typ, pattern in [('метод',r'метод|model|модел|алгоритм|learning'),('ресурс',r'ресурс|оборуд|gpu|лаборатор|budget|equipment'),('срок-этап',r'месяц|этап|month|срок'),('результат',r'результат|публикац|deliverable|dataset'),('цель',r'цель|aim|objective')]:
            if re.search(pattern,s,re.I): kind=typ; break
        reqs.append(dict(id=f'R{i}',summary_en=s,quote=s,type=kind,importance='ключевое',stated='прямо',section='Текст Proposal'))
    ev=[]
    for source,text in texts.items():
        for s in re.split(r'\n+|(?<=[.!?])\s+',text):
            if len(s.strip())>15:
                ev.append(dict(id=f'E{len(ev)+1}',summary_en=s.strip(),quote=s.strip(),source=source,type='область',strength='упоминание',year=''))
    # Tokenize evidence once; candidate lookup uses an inverted index for long proposals.
    ev_tokens={e['id']:tokens(e['quote']) for e in ev}
    postings={}
    for ident, words in ev_tokens.items():
        for word in words: postings.setdefault(word,set()).add(ident)
    matrix=[]
    for r in reqs:
        rt=tokens(r['quote'])
        candidates=set().union(*(postings.get(t,set()) for t in rt)) if rt else set()
        matches=sorted([(len(rt & ev_tokens[ident])/max(1,len(rt)), ident) for ident in candidates],key=lambda x:(-x[0],x[1]))[:3]
        used=[ident for score,ident in matches if score>=0.25 and len(rt & ev_tokens[ident])>=2]
        matrix.append(dict(req_id=r['id'],status=STATUSES[3] if used else STATUSES[2],evidence_ids=used,
             rationale='Найдены только текстовые совпадения. Соответствие компетенций должен оценить эксперт.' if used else 'Текстовых совпадений не найдено. Это не доказывает отсутствие опыта.',question=f'Какие работы и результаты подтверждают: «{r["quote"]}»?'))
    info=dict(requirements=reqs,missing=[x for x in ['метод','ресурс','срок-этап','результат'] if x not in {r['type'] for r in reqs}],language='не определён',self_claims=[])
    matrix=core.check_matches(matrix,reqs,ev)
    risks=[dict(risk='Соответствие требует содержательной проверки экспертом.',level='средний',basis='Локальный режим использует только совпадения слов.')]
    rs=dict(summary='Предварительный обзор материалов. Пункты Proposal выделены по предложениям; совпадения с CV и дополнительными материалами найдены по словам. Семантическая AI-оценка не выполнялась. Ни один навык автоматически не подтверждён. Решение принимает эксперт.',risks=risks,questions=[m['question'] for m in matrix])
    return info,ev,matrix,rs,[]


def verify_quotes(info, evidence, matrix, texts, proposal):
    bad=set()
    for e in evidence:
        origin = proposal if e['source']=='Proposal (самоописание)' else texts.get(e['source'],'')
        e['quote'],e['quote_check']=locate_quote(e['quote'],origin)
        e['quote_verified']=e['quote_check']!='not_found'
        if not e['quote_verified']: bad.add(e['id'])
    for requirement in info['requirements']:
        requirement['quote'],method=locate_quote(requirement['quote'],proposal)
        requirement['quote_verified']=method!='not_found'
    for m in matrix:
        requirement=next(r for r in info['requirements'] if r['id']==m['req_id'])
        m['quote']=requirement['quote']
        m['quote_verified']=requirement['quote_verified']
        if not m['quote_verified'] or bad.intersection(m['evidence_ids']):
            m['status']=STATUSES[3]
            reasons=[]
            if not m['quote_verified']:reasons.append('Не найдена цитата требования '+m['req_id']+' в Proposal')
            invalid=sorted(bad.intersection(m['evidence_ids']))
            if invalid:reasons.append('Не найдены цитаты доказательств: '+', '.join(invalid))
            m['auto_check']='; '.join(filter(None,[m.get('auto_check',''),*reasons]))
            m['question']=m.get('question') or 'Уточните исходный фрагмент и подтверждение этого требования.'


def analyze(payload, update=lambda s: None):
    started=time.monotonic()
    proposal=payload['proposal']; texts={'CV':payload['cv']}
    if payload.get('application'): texts['заявка']=payload['application']
    if payload.get('extra'): texts['дополнительные материалы']=payload['extra']
    if payload.get('profiles'): texts['идентификаторы']=payload['profiles']
    update('Извлечение требований и опыта')
    if payload['mode']=='ai':
        llm=CheckedLLM()
        update('Подготовка локальной модели поиска и эмбеддингов')
        try:
            retriever=HybridRetriever(texts)
        except Exception:
            raise AIError('Не удалось подготовить локальную embedding-модель. Выполните python retrieval.py --prepare через Python проекта. При первой загрузке нужен доступ к Hugging Face; после загрузки модель работает локально.') from None
        update('Извлечение требований и опыта из документов')
        sources=[source for source in texts if source!='идентификаторы']
        with ThreadPoolExecutor(max_workers=2) as workers:
            requirement_job=workers.submit(core.extract_requirements,llm,proposal)
            document_jobs={source:workers.submit(core.extract_evidence,llm,texts[source],source) for source in sources}
            info=requirement_job.result()
            blocks={source:future.result() for source,future in document_jobs.items()}
        if not info['requirements']: raise ValueError('Модель не выделила требований. Уточните Proposal.')
        cv=blocks['CV']
        extra_blocks=[block for source,block in blocks.items() if source!='CV']
        extra=dict(evidence=[e for block in extra_blocks for e in block['evidence']],claimed_metrics=[m for block in extra_blocks for m in block['claimed_metrics']])
        ev=core.build_evidence_table(cv,extra,info['self_claims'],[])
        update('Проверка цитат и подготовка фрагментов')
        verify_quotes(info,ev,[],texts,proposal)
        verified_ev=[e for e in ev if e['quote_verified']]
        for evidence in verified_ev:
            for fragment in retriever.fragments:
                if fragment['source']==evidence['source'] and locate_quote(evidence['quote'],fragment['text'])[1]!='not_found':
                    evidence.update(fragment_id=fragment['id'],source_start=fragment['start'],source_end=fragment['end'],context=fragment['text'])
                    break
        try:
            update('Гибридный поиск: смысл и научные термины')
            retrieved=retriever.search_many(info['requirements'])
        except Exception:
            raise AIError('Не удалось выполнить гибридный поиск: ошибка вычисления эмбеддингов. Проверьте локальную модель командой python retrieval.py --prepare через Python проекта.') from None
        update('Оценка найденного контекста по каждому требованию')
        matrix,new_ev=match_retrieved(llm,info['requirements'],verified_ev,retrieved,reserved_ids={e['id'] for e in ev})
        ev+= [e for e in new_ev if e['id'] not in {item['id'] for item in ev}]
        verify_quotes(info,ev,matrix,texts,proposal)
        update('Подготовка рисков и справки')
        claimed=cv.get('claimed_metrics',[])+(extra or {}).get('claimed_metrics',[])
        rs=core.risks_and_summary(llm,matrix,info,None,claimed)
        usage=llm.log
    else:
        info,ev,matrix,rs,claimed=preliminary(proposal,texts); usage=[]
        verify_quotes(info,ev,matrix,texts,proposal)
    profile={'links':core.find_profiles(texts),'warnings':['Внешние научные базы не запрашивались. Идентификаторы и публикации из документов требуют проверки.'],'metrics_by_source':{},'other_links':link_inventory(texts)}
    source_links=profile['other_links']
    report=core.make_report(payload.get('candidate') or 'Кандидат',info,matrix,ev,rs,profile,claimed)
    mode='AI-анализ' if payload['mode']=='ai' else 'Предварительный локальный анализ (без AI)'
    report=f'> Режим: {mode}\n\n'+report
    if source_links:
        report+='\n\n## Ссылки из материалов\n\n'+'\n'.join(f"- {x['type']}: {x['url']} (источник: {', '.join(x['sources'])}; содержимое не загружалось)" for x in source_links)
    return dict(duration_seconds=round(time.monotonic()-started,2),retrieval_method={'name':'multilingual embeddings + BM25 + RRF','embedding_model':EMBEDDING_MODEL,'top_k':6,'fragment_count':len(retriever.fragments)} if payload['mode']=='ai' else None,source_links=source_links,candidate=payload.get('candidate') or 'Кандидат',mode=payload['mode'],requirements=info,evidence=ev,matrix=matrix,risks=rs,profile=profile,report=report,usage=usage)

@app.get('/')
def index(): return FileResponse(ROOT/'static/index.html',headers={'Cache-Control':'no-cache'})

@app.get('/api/health')
def health(): return {'ok':True,'ai_available':bool(os.getenv('NEURAL_DEEP_API_KEY','').strip()),'model':os.getenv('PROPOSAL_MODEL',DEFAULT_MODEL)}

async def ai_request(request):
    # Reject cross-origin requests to the local credential/configuration endpoints.
    origin=request.headers.get('origin')
    if origin and origin.rstrip('/')!=str(request.base_url).rstrip('/'):
        raise HTTPException(403,'Откройте настройки с той же страницы локального сайта.')
    if not request.headers.get('content-type','').startswith('application/json'):
        raise HTTPException(415,'Ожидается JSON.')
    try: data=await request.json()
    except Exception: raise HTTPException(400,'Некорректный JSON.')
    if not isinstance(data,dict): raise HTTPException(422,'Ожидается объект настроек.')
    for k in ('api_key','model'):
        if not isinstance(data.get(k,''),str) or len(data.get(k,''))>2000:
            raise HTTPException(422,'Некорректный формат настройки.')
    return data

@app.post('/api/ai/connect')
async def ai_connect(request: Request):
    from starlette.concurrency import run_in_threadpool
    data=await ai_request(request)
    with lock:
        if any(j['status']=='running' for j in jobs.values()):
            raise HTTPException(409,'Настройки можно изменить после завершения анализа.')
    def connect():
        try:
            key=data.get('api_key','').strip() or os.getenv('NEURAL_DEEP_API_KEY','')
            # Saving a key must not depend on the optional /models endpoint.
            if not key.strip(): raise AIError('Введите ключ Neural Deep или задайте NEURAL_DEEP_API_KEY в .env.')
            os.environ['NEURAL_DEEP_API_KEY']=key
            selected=os.getenv('PROPOSAL_MODEL',DEFAULT_MODEL)
            return dict(models=[],model=selected,message='Ключ сохранён. Выберите модель и проверьте AI.')
        except Exception as exc:
            raise HTTPException(422,safe_error(exc)) from None
    return await run_in_threadpool(connect)

@app.get('/api/ai/models')
async def ai_models():
    from starlette.concurrency import run_in_threadpool
    def load():
        try:
            with make_client().with_options(timeout=10) as client:
                return {'models':sorted({m.id for m in client.models.list().data})}
        except Exception as exc: raise HTTPException(422,safe_error(exc)) from None
    return await run_in_threadpool(load)

@app.post('/api/ai/check')
async def ai_check(request: Request):
    from starlette.concurrency import run_in_threadpool
    data=await ai_request(request)
    if not data.get('model','').strip(): raise HTTPException(422,'Введите название модели.')
    with lock:
        if any(j['status']=='running' for j in jobs.values()): raise HTTPException(409,'Дождитесь завершения анализа.')
    def check():
        try:
            started=time.monotonic()
            llm=CheckedLLM(model=data['model'])
            schema={'type':'object','additionalProperties':False,'required':['answer'],'properties':{'answer':{'type':'string'}}}
            llm.json('Проверка структурированного ответа.','Верни объект с полем answer и строкой OK.',schema,'connection_test')
            os.environ['PROPOSAL_MODEL']=data['model']
            return dict(ok=True,model=data['model'],message=f'AI отвечает в JSON за {time.monotonic()-started:.1f} с. Модель: {data["model"]}. Можно запускать анализ документов.')
        except Exception as exc: raise HTTPException(422,safe_error(exc)) from None
    return await run_in_threadpool(check)

@app.post('/api/extract')
async def extract(request: Request):
    form=await request.form(); file=form.get('file')
    if not file or not hasattr(file,'read'): raise HTTPException(400,'Выберите файл.')
    suffix=Path(file.filename or '').suffix.lower()
    if suffix not in {'.pdf','.docx','.xlsx','.txt','.md','.csv'}: raise HTTPException(400,'Поддерживаются PDF, DOCX, XLSX, TXT, MD и CSV.')
    raw=await file.read(LIMIT+1)
    if len(raw)>LIMIT: raise HTTPException(413,'Максимальный размер файла — 8 МБ.')
    try:
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/('document'+suffix); p.write_bytes(raw); text=read_xlsx(p) if suffix=='.xlsx' else core.read_file(str(p))
        if not text.strip(): raise ValueError('Не найден текст. Для скана сначала выполните OCR или вставьте текст вручную.')
        if len(text)>120000: raise ValueError('Документ слишком большой: максимум 120 000 символов.')
        return {'text':text,'links':link_inventory({'документ':text})}
    except Exception as e:
        raise HTTPException(422,str(e) if isinstance(e,ValueError) else 'Не удалось прочитать документ. Проверьте формат и отсутствие пароля.') from None

@app.post('/api/analyze',status_code=202)
async def start(request: Request):
    try: p=await request.json()
    except Exception: raise HTTPException(400,'Некорректный JSON.')
    if not isinstance(p,dict): raise HTTPException(422,'Ожидается объект с материалами.')
    for key in ['cv','proposal']:
        if not isinstance(p.get(key),str) or len(p[key].strip())<20: raise HTTPException(422,'Добавьте CV и Proposal: не менее 20 символов каждый.')
    for key in ['cv','proposal','application','extra','profiles','candidate']:
        if not isinstance(p.get(key,''),str) or len(p.get(key,''))>120000: raise HTTPException(422,'Недопустимый тип или размер поля.')
    if p.get('mode') not in ['local','ai']: raise HTTPException(422,'Выберите режим анализа.')
    if p['mode']=='ai' and not os.getenv('NEURAL_DEEP_API_KEY'): raise HTTPException(409,'Для AI-анализа настройте NEURAL_DEEP_API_KEY на сервере. Доступен локальный режим.')
    with lock:
        for k in list(jobs):
            if time.time()-jobs[k]['created']>3600 and jobs[k]['status']!='running': del jobs[k]
        if any(j['status']=='running' for j in jobs.values()): raise HTTPException(429,'Другой анализ ещё выполняется. Дождитесь его завершения.')
        if len(jobs)>=20: raise HTTPException(429,'Лимит сессий достигнут. Удалите предыдущие результаты или перезапустите сервер.')
        ident=uuid.uuid4().hex; jobs[ident]={'status':'running','stage':'Подготовка материалов','model':os.getenv('PROPOSAL_MODEL',DEFAULT_MODEL) if p['mode']=='ai' else '', 'created':time.time()}
    def run():
        try:
            result=analyze(p,lambda s: jobs[ident].update(stage=s))
            jobs[ident].update(status='done',result=result)
        except Exception as e:
            msg=str(e) if isinstance(e,ValueError) else safe_error(e)
            jobs[ident].update(status='error',error=msg,failed_stage=jobs[ident]['stage'])
    pool.submit(run)
    return {'id':ident}

@app.get('/api/jobs/{ident}')
def job(ident:str):
    if ident not in jobs: raise HTTPException(404,'Анализ не найден или удалён.')
    return jobs[ident]

@app.delete('/api/jobs/{ident}')
def delete(ident:str):
    if ident in jobs and jobs[ident]['status']=='running': raise HTTPException(409,'Дождитесь завершения анализа.')
    jobs.pop(ident,None); return {'ok':True}

@app.get('/api/jobs/{ident}/export/{fmt}')
def export(ident:str,fmt:str):
    result=job(ident).get('result')
    if not result: raise HTTPException(409,'Результат ещё не готов.')
    if fmt=='json': data=json.dumps(result,ensure_ascii=False,indent=2).encode(); mime='application/json'
    elif fmt=='md': data=result['report'].encode(); mime='text/markdown; charset=utf-8'
    elif fmt=='xlsx':
        wb=Workbook(); wb.remove(wb.active)
        sheets={'Матрица':result['matrix'],'Требования':result['requirements']['requirements'],'Опыт':result['evidence'],'Риски':result['risks']['risks'],'Вопросы':[{'Вопрос':q} for q in result['risks']['questions']],'Ссылки':result.get('source_links',[])}
        for name,rows in sheets.items():
            ws=wb.create_sheet(name)
            if rows:
                keys=list(rows[0]); ws.append(keys)
                for row in rows:
                    vals=[json.dumps(row.get(k,''),ensure_ascii=False) if isinstance(row.get(k), (list,dict)) else row.get(k,'') for k in keys]
                    ws.append(vals)
                    for cell in ws[ws.max_row]:
                        if isinstance(cell.value,str): cell.data_type='s'
                ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
        buf=io.BytesIO(); wb.save(buf); data=buf.getvalue(); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    else: raise HTTPException(404,'Неизвестный формат.')
    return Response(data,media_type=mime,headers={'Content-Disposition':f'attachment; filename="proposal-fit-report.{fmt}"','Cache-Control':'no-store'})
