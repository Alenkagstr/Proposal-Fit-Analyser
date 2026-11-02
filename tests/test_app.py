import io,time
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import app

client=TestClient(app.app)
PAYLOAD=dict(cv='Python и PyTorch: проект сегментации медицинских изображений в 2024 году.\nСтатистический анализ результатов проекта.',proposal='Метод: сегментация медицинских изображений на Python и PyTorch.\nРесурс: доступ к лаборатории и GPU.\nЭтап: валидация за 12 месяцев.',mode='local',candidate='C01')

@pytest.fixture(autouse=True)
def local_search_without_model_download(monkeypatch):
 from retrieval import HybridRetriever
 class Encoder:
  def passage_embed(self,texts,**kwargs):return [[1.,float(len(t)%7+1)] for t in texts]
  query_embed=passage_embed
 monkeypatch.setattr(app,'HybridRetriever',lambda texts:HybridRetriever(texts,encoder=Encoder()))


def wait_result(ident):
 for _ in range(200):
  r=client.get('/api/jobs/'+ident).json()
  if r['status']!='running': return r
  time.sleep(.01)
 pytest.fail('Job did not complete')


def test_local_analysis_exports_and_delete():
 r=client.post('/api/analyze',json=PAYLOAD)
 assert r.status_code==202
 ident=r.json()['id']; j=wait_result(ident)
 assert j['status']=='done', j
 result=j['result']
 assert len(result['matrix'])==3
 assert result['matrix'][0]['evidence_ids']
 assert result['matrix'][0]['status']=='требует экспертной проверки'
 assert result['matrix'][1]['status']=='не найдено в материалах'
 assert all(m['status']!='подтверждено' for m in result['matrix'])
 assert result['risks']['questions']
 assert 'Предварительный' in result['report']
 assert client.get(f'/api/jobs/{ident}/export/json').json()['matrix']==result['matrix']
 assert 'Аналитическая справка' in client.get(f'/api/jobs/{ident}/export/md').text
 excel=client.get(f'/api/jobs/{ident}/export/xlsx')
 wb=load_workbook(io.BytesIO(excel.content))
 assert wb.sheetnames==['Матрица','Требования','Опыт','Риски','Вопросы','Ссылки']
 assert wb['Матрица'].max_row==4
 assert client.delete('/api/jobs/'+ident).status_code==200
 assert client.get('/api/jobs/'+ident).status_code==404


def test_file_parsing():
 r=client.post('/api/extract',files={'file':('cv.txt','Python и опыт разработки научных моделей'.encode())})
 assert r.status_code==200 and 'Python' in r.json()['text']
 assert client.post('/api/extract',files={'file':('bad.exe',b'bad')}).status_code==400
 assert client.post('/api/extract',files={'file':('empty.txt',b'')}).status_code==422
 assert client.post('/api/extract',files={'file':('broken.pdf',b'broken')}).status_code==422
 assert client.post('/api/extract',files={'file':('large.txt',b'x'*(app.LIMIT+1))}).status_code==413
 from docx import Document
 doc=Document();doc.add_paragraph('Scientific experience in Python and imaging');buf=io.BytesIO();doc.save(buf)
 r=client.post('/api/extract',files={'file':('cv.docx',buf.getvalue())})
 assert 'Scientific experience' in r.json()['text']


def test_input_and_missing_key(monkeypatch):
 monkeypatch.delenv('NEURAL_DEEP_API_KEY',raising=False)
 assert client.get('/api/health').json()['ai_available'] is False
 assert client.post('/api/analyze',json={**PAYLOAD,'mode':'ai'}).status_code==409
 assert client.post('/api/analyze',json={**PAYLOAD,'cv':''}).status_code==422
 assert client.post('/api/analyze',json=[]).status_code==422
 assert client.post('/api/analyze',content='invalid').status_code==400


def test_guardrails_and_quote_validation():
 req=dict(id='R1',summary_en='Python models',quote='Python models',type='метод',importance='ключевое',stated='прямо')
 match=dict(req_id='R1',status='подтверждено',evidence_ids=['fake'],rationale='yes',question='')
 matrix=app.core.check_matches([match],[req],[])
 assert matrix[0]['status']=='требует экспертной проверки'
 ev=dict(id='E1',source='CV',strength='проект',quote='Invented proof')
 matrix=app.core.check_matches([{**match,'evidence_ids':['E1']}],[req],[ev])
 app.verify_quotes({'requirements':[req]},[ev],matrix,{'CV':'Actual text'},'Python models')
 assert matrix[0]['status']=='требует экспертной проверки'
 assert ev['quote_verified'] is False


def test_ai_pipeline_with_mock(monkeypatch):
 class Fake:
  log=[]
  def json(self,system,user,schema,name,**kwargs):
   if name=='requirements': return dict(language='RU',requirements=[dict(summary_en='Python models',type='метод',importance='ключевое',stated='прямо',quote='Python и PyTorch',section='Метод')],missing=[],self_claims=[])
   if name=='evidence_CV': return dict(language='RU',evidence=[dict(summary_en='Python models',type='проект',strength='проект',year='2024',quote='Python и PyTorch')],claimed_metrics=[])
   if name=='matching': return dict(matches=[dict(req_id='R1',status='подтверждено',evidence_ids=['E1'],rationale='Пример проекта',question='')])
   if name=='risks_summary': return dict(summary='Опыт подтверждён материалами.',risks=[],questions=[])
   raise AssertionError(name)
 monkeypatch.setattr(app,'CheckedLLM',Fake)
 result=app.analyze({**PAYLOAD,'mode':'ai'})
 assert result['mode']=='ai' and result['matrix'][0]['status']=='подтверждено'
 assert result['evidence'][0]['quote_verified']


def test_excel_formula_is_text():
 result=app.analyze(PAYLOAD);result['matrix'][0]['requirement']='=HYPERLINK("http://example.com")'
 app.jobs['formula-test']={'status':'done','result':result,'created':time.time()}
 wb=load_workbook(io.BytesIO(client.get('/api/jobs/formula-test/export/xlsx').content))
 cells=[c for row in wb['Матрица'] for c in row if c.value=='=HYPERLINK("http://example.com")']
 assert cells and cells[0].data_type=='s'
 app.jobs.pop('formula-test')


def test_more_than_100_requirements_not_truncated():
 p={**PAYLOAD,'proposal':'\n'.join(f'Метод номер {i}: анализ медицинских изображений на Python.' for i in range(160))}
 result=app.analyze(p)
 assert len(result['matrix'])==160
 assert result['matrix'][-1]['req_id']=='R160'


def test_application_evidence_and_links():
 text='Проект: статистический анализ медицинских изображений в 2024 году.\nORCID: https://orcid.org/0000-0002-1825-0097\nКод: https://github.com/example/research'
 result=app.analyze({**PAYLOAD,'application':text,'extra':'Дополнительно: подготовка наборов данных для машинного обучения.'})
 assert any(e['source']=='заявка' for e in result['evidence'])
 assert any(e['source']=='дополнительные материалы' for e in result['evidence'])
 links=result['source_links']
 assert any(x['url']=='https://github.com/example/research' and x['sources']==['заявка'] for x in links)
 assert any(x['type']=='orcid' for x in links)
 assert 'Ссылки из материалов' in result['report']


def test_hidden_links_in_xlsx_and_docx():
 from openpyxl import Workbook
 wb=Workbook();ws=wb.active;ws['A1']='Google Scholar';ws['A1'].hyperlink='https://scholar.google.com/citations?user=abcdefghijkl'
 ws['B1']='=HYPERLINK("https://example.org/publications", "Publications")'
 b=io.BytesIO();wb.save(b)
 r=client.post('/api/extract',files={'file':('application.xlsx',b.getvalue())})
 assert r.status_code==200,r.text
 assert len(r.json()['links'])==2
 assert 'https://example.org/publications' in r.json()['text']
 from docx import Document
 from docx.oxml import OxmlElement
 from docx.oxml.ns import qn
 from docx.opc.constants import RELATIONSHIP_TYPE as RT
 d=Document();p=d.add_paragraph('Научные профили: ')
 rel=d.part.relate_to('https://orcid.org/0000-0002-1825-0097',RT.HYPERLINK,is_external=True)
 h=OxmlElement('w:hyperlink');h.set(qn('r:id'),rel)
 run=OxmlElement('w:r');t=OxmlElement('w:t');t.text='ORCID';run.append(t);h.append(run);p._p.append(h)
 b=io.BytesIO();d.save(b)
 r=client.post('/api/extract',files={'file':('application.docx',b.getvalue())})
 assert r.status_code==200 and any(x['type']=='orcid' for x in r.json()['links'])


def test_pdf_hidden_uri():
 from pypdf import PdfWriter
 from pypdf.annotations import Link
 b=io.BytesIO();w=PdfWriter();w.add_blank_page(width=300,height=300)
 w.add_annotation(0,Link(rect=(0,0,100,100),url='https://example.org/research'))
 w.write(b)
 r=client.post('/api/extract',files={'file':('application.pdf',b.getvalue())})
 assert r.status_code==200,r.text
 assert r.json()['links'][0]['url']=='https://example.org/research'


def test_summary_matrix_not_used_as_evidence():
 from openpyxl import Workbook
 w=Workbook();w.active.append(['Кандидат','Поле','В заявке','В CV']);w.active.append(['C01','Google Scholar',1,0]);b=io.BytesIO();w.save(b)
 r=client.post('/api/extract',files={'file':('summary.xlsx',b.getvalue())})
 assert r.status_code==422 and 'сводка' in r.json()['detail']


def test_ai_connect_and_check_without_leaking_key(monkeypatch):
 from types import SimpleNamespace as N
 monkeypatch.delenv('NEURAL_DEEP_API_KEY',raising=False)
 monkeypatch.setenv('PROPOSAL_MODEL','test-model')
 monkeypatch.setattr(app,'make_client',lambda key:N(models=N(list=lambda:N(data=[N(id='test-model')]))))
 r=client.post('/api/ai/connect',json={'api_key':'test-private-value'})
 assert r.status_code==200 and r.json()['models']==[]
 assert r.json()['model']=='test-model'
 assert 'test-private-value' not in r.text
 class Fake:
  def __init__(self,**kwargs):pass
  def json(self,*args):return {'answer':'OK'}
 monkeypatch.setattr(app,'CheckedLLM',Fake)
 r=client.post('/api/ai/check',json={'model':'test-model'})
 assert r.status_code==200 and r.json()['ok']
 assert client.post('/api/ai/connect',headers={'Origin':'https://attacker.example'},json={}).status_code==403
 assert client.post('/api/ai/connect',data={'api_key':'bad'}).status_code==415


def test_ai_error_is_actionable_and_redacted(monkeypatch):
 import httpx,openai
 request=httpx.Request('GET','https://api.neuraldeep.tech/v1/models')
 def fail(key):
  raise openai.AuthenticationError('secret=should-never-leak',response=httpx.Response(401,request=request),body=None)
 monkeypatch.setattr(app,'CheckedLLM',lambda **kwargs:fail('unused'))
 r=client.post('/api/ai/check',json={'model':'test-model'})
 assert r.status_code==422 and '401' in r.text
 assert 'should-never-leak' not in r.text and 'also-private' not in r.text


def test_ai_json_fallback_and_fail_fast(monkeypatch):
 from types import SimpleNamespace as N
 import ai_service,openai,httpx
 calls=[]
 def completion(**kwargs):
  calls.append(kwargs)
  if len(calls)==1:
   return N(choices=[N(finish_reason='stop',message=N(content='not JSON'))],usage=None)
  return N(choices=[N(finish_reason='stop',message=N(content='{"answer":"OK"}'))],usage=None)
 monkeypatch.setattr(ai_service,'make_client',lambda key:N(chat=N(completions=N(create=completion))))
 llm=ai_service.CheckedLLM(api_key='unused',model='test')
 value=llm.json('system','data',{'type':'object','required':['answer'],'properties':{'answer':{'type':'string'}}},'evidence_заявка')
 assert value=={'answer':'OK'} and len(calls)==2
 assert 'response_format' not in calls[0]
 assert calls[1]['response_format']=={'type':'json_object'}
 assert 'required' in calls[0]['messages'][0]['content']
 calls.clear()
 def unauthorized(**kwargs):
  calls.append(kwargs)
  raise openai.AuthenticationError('private',response=httpx.Response(401,request=httpx.Request('POST','https://api.neuraldeep.tech/v1/chat/completions')),body=None)
 llm.client.chat.completions.create=unauthorized
 with pytest.raises(ai_service.AIError,match='401'):llm.json('sys','user',{},'test')
 assert len(calls)==1


def test_ai_uses_application_as_separate_source(monkeypatch):
 seen=[]
 class Fake:
  log=[]
  def json(self,system,user,schema,name,**kwargs):
   seen.append(user)
   if name=='requirements':return dict(language='RU',requirements=[dict(summary_en='Python models',type='метод',importance='ключевое',stated='прямо',quote='Python и PyTorch',section='Метод')],missing=[],self_claims=[])
   if name.startswith('evidence_'):return dict(language='RU',evidence=[dict(summary_en='Python models',type='проект',strength='проект',year='2024',quote='Python и PyTorch')],claimed_metrics=[])
   if name=='matching':return dict(matches=[dict(req_id='R1',status='подтверждено',evidence_ids=['E2'],rationale='Из заявки',question='')])
   return dict(summary='Справка',risks=[],questions=[])
 monkeypatch.setattr(app,'CheckedLLM',Fake)
 result=app.analyze({**PAYLOAD,'mode':'ai','application':'Заявка: Python и PyTorch. https://example.org/profile'})
 assert result['evidence'][1]['source']=='заявка'
 assert result['evidence'][1]['quote_verified']
 assert result['matrix'][0]['status']=='подтверждено'
 assert any('Документ кандидата (заявка)' in x for x in seen)


def test_connect_does_not_request_models(monkeypatch):
 from types import SimpleNamespace as N
 def forbidden(): raise AssertionError('models must be optional')
 monkeypatch.setattr(app,'make_client',lambda key:N(models=N(list=forbidden)))
 monkeypatch.setenv('NEURAL_DEEP_API_KEY','old')
 r=client.post('/api/ai/connect',json={'api_key':'new-private'})
 assert r.status_code==200 and 'new-private' not in r.text
 assert app.os.environ['NEURAL_DEEP_API_KEY']=='new-private'


def test_job_error_preserves_failed_stage(monkeypatch):
 def fail(payload,update):
  update('Извлечение опыта CV')
  raise app.AIError('Neural Deep не ответил вовремя.')
 monkeypatch.setattr(app,'analyze',fail)
 r=client.post('/api/analyze',json=PAYLOAD)
 j=wait_result(r.json()['id'])
 assert j['status']=='error' and j['failed_stage']=='Извлечение опыта CV'
 assert 'не ответил' in j['error']
 client.delete('/api/jobs/'+r.json()['id'])


def test_ai_deadline_and_timeout_no_retry(monkeypatch):
 import ai_service,openai,httpx
 from types import SimpleNamespace as N
 calls=[]
 def slow(**kwargs):
  calls.append(kwargs)
  raise openai.APITimeoutError(request=httpx.Request('POST','https://api.neuraldeep.tech/v1/chat/completions'))
 monkeypatch.setattr(ai_service,'make_client',lambda key:N(chat=N(completions=N(create=slow))))
 llm=ai_service.CheckedLLM(api_key='unused')
 with pytest.raises(ai_service.AIError,match='Тайм-аут ожидания ответа'):
  llm.json('system','user',{},'requirements')
 assert len(calls)==1 and 0<calls[0]['timeout']<=180
 probe=ai_service.CheckedLLM(api_key='unused')
 with pytest.raises(ai_service.AIError):probe.json('system','user',{},'connection_test')
 assert calls[-1]['timeout']<=30 and calls[-1]['max_tokens']==8000
 calls.pop()
 llm.deadline=0
 with pytest.raises(ai_service.AIError,match='10 минут'):
  llm.json('system','user',{},'requirements')
 assert len(calls)==1


def test_optional_models_failure_keeps_manual_configuration(monkeypatch):
 import openai,httpx
 from types import SimpleNamespace as N
 def fail():
  raise openai.APITimeoutError(request=httpx.Request('GET','https://api.neuraldeep.tech/v1/models'))
 class Fake:
  def __enter__(self):return self
  def __exit__(self,*args):pass
  def with_options(self,**kwargs):
   assert kwargs['timeout']==10
   return self
  models=N(list=fail)
 monkeypatch.setattr(app,'make_client',lambda:Fake())
 monkeypatch.setenv('NEURAL_DEEP_API_KEY','existing-private')
 r=client.get('/api/ai/models')
 assert r.status_code==422 and 'existing-private' not in r.text
 assert app.os.environ['NEURAL_DEEP_API_KEY']=='existing-private'
 class Check:
  def __init__(self,**kwargs):assert kwargs['model']=='qwen3.6-35b-a3b'
  def json(self,*args):return {'answer':'OK'}
 monkeypatch.setattr(app,'CheckedLLM',Check)
 monkeypatch.setenv('PROPOSAL_MODEL','previous-model')
 assert client.post('/api/ai/check',json={'model':'qwen3.6-35b-a3b'}).status_code==200


def test_real_sdk_plain_request_and_timeout_diagnostics(monkeypatch):
 import ai_service,openai,httpx,json
 requests=[]
 def respond(request):
  requests.append(json.loads(request.content))
  return httpx.Response(200,json={'id':'chat-test','object':'chat.completion','created':0,'model':'qwen3.6-35b-a3b','choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{"answer":"OK"}'}}]})
 with openai.OpenAI(api_key='private-test-key',base_url='https://api.neuraldeep.tech/v1',http_client=httpx.Client(transport=httpx.MockTransport(respond))) as sdk:
  monkeypatch.setattr(ai_service,'make_client',lambda key:sdk)
  llm=ai_service.CheckedLLM()
  assert llm.json('sys','doc',{'type':'object','required':['answer'],'properties':{'answer':{'type':'string'}}},'requirements')=={'answer':'OK'}
 assert len(requests)==1
 assert 'response_format' not in requests[0]
 assert requests[0]['model']=='qwen3.6-35b-a3b'
 assert 'required' in requests[0]['messages'][0]['content']
 for timeout,label in [(httpx.ConnectTimeout,'соединения'),(httpx.ReadTimeout,'ожидания ответа'),(httpx.WriteTimeout,'отправки')]:
  def fail(request):raise timeout('private-test-key and private-document',request=request)
  with openai.OpenAI(api_key='private-test-key',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(fail))) as sdk:
   monkeypatch.setattr(ai_service,'make_client',lambda key:sdk)
   with pytest.raises(ai_service.AIError) as caught:
    ai_service.CheckedLLM().json('sys','private-document',{},'requirements')
   message=str(caught.value)
   assert label in message and 'Формат: text' in message
   assert 'private-test-key' not in message and 'private-document' not in message


def test_connection_check_token_limit_and_truncation(monkeypatch):
 import ai_service
 from types import SimpleNamespace as N
 calls=[]
 def completion(**kwargs):
  calls.append(kwargs)
  return N(choices=[N(finish_reason='length',message=N(content=''))])
 monkeypatch.setattr(ai_service,'make_client',lambda key:N(chat=N(completions=N(create=completion))))
 llm=ai_service.CheckedLLM(api_key='test')
 with pytest.raises(ai_service.AIError,match='Соединение с Neural Deep установлено') as caught:
  llm.json('system','short check',{},'connection_test')
 assert 'Сократите материалы' not in str(caught.value)
 assert calls[0]['max_tokens']==8000 and calls[0]['timeout']<=30
 assert len(calls)==1


def test_matching_receives_only_verified_full_evidence(monkeypatch):
 seen=[]
 quote='Python и PyTorch '+('подробности проекта '*15)+'уточнение: метод применён на практике'
 payload={**PAYLOAD,'mode':'ai','cv':quote}
 class Fake:
  log=[]
  def json(self,system,user,schema,name,**kwargs):
   if name=='requirements': return dict(language='RU',requirements=[dict(summary_en='Python models',type='метод',importance='ключевое',stated='прямо',quote='Python и PyTorch',section='Метод')],missing=[],self_claims=[])
   if name=='evidence_CV':return dict(language='RU',evidence=[dict(summary_en='Python models',type='проект',strength='проект',year='2024',quote=quote),dict(summary_en='Invented',type='проект',strength='проект',year='',quote='fabricated source evidence')],claimed_metrics=[])
   if name=='matching':
    seen.append(user)
    return dict(matches=[dict(req_id='R1',status='подтверждено',evidence_ids=['E1'],rationale='Проект',question='')])
   return dict(summary='Справка',risks=[],questions=[])
 monkeypatch.setattr(app,'CheckedLLM',Fake)
 result=app.analyze(payload)
 assert quote in seen[0] and 'fabricated source evidence' not in seen[0]
 assert result['matrix'][0]['status']=='подтверждено'
 assert result['evidence'][1]['quote_verified'] is False


def test_hybrid_pipeline_recovers_experience_missed_by_initial_extraction(monkeypatch):
 quote='В проекте применила PheWAS для анализа фенотипических связей.'
 class Fake:
  log=[]
  def json(self,system,user,schema,name,**kwargs):
   if name=='requirements':return dict(language='RU',requirements=[dict(summary_en='Conduct PheWAS',type='метод',importance='ключевое',stated='прямо',quote='Провести PheWAS анализ',section='Метод')],missing=[],self_claims=[])
   if name=='evidence_CV':return dict(language='RU',evidence=[],claimed_metrics=[])
   if name=='matching':
    assert quote in user
    return dict(matches=[dict(req_id='R1',status='подтверждено',evidence_ids=[],rationale='Работа в проекте.',question='',supports=[dict(fragment_id='F1',quote=quote,strength='проект',type='проект',summary_en='PheWAS study')])])
   return dict(summary='Справка',risks=[],questions=[])
 monkeypatch.setattr(app,'CheckedLLM',Fake)
 result=app.analyze({**PAYLOAD,'cv':quote,'proposal':'Метод: Провести PheWAS анализ в ходе проекта.','mode':'ai'})
 assert result['matrix'][0]['status']=='подтверждено'
 assert result['evidence'][0]['quote']==quote
 assert result['evidence'][0]['source']=='CV'
 assert result['evidence'][0]['fragment_id']=='F1'
 assert result['matrix'][0]['retrieval'][0]['fragment_id']=='F1'
 assert result['retrieval_method']['top_k']==6
 monkeypatch.setenv('NEURAL_DEEP_API_KEY','test-only')
 response=client.post('/api/analyze',json={**PAYLOAD,'cv':quote,'proposal':'Метод: Провести PheWAS анализ в ходе проекта.','mode':'ai'})
 ident=response.json()['id']
 job=wait_result(ident)
 assert job['status']=='done'
 exported=client.get('/api/jobs/'+ident+'/export/json').json()
 assert exported['evidence'][0]['context']==quote
 workbook=load_workbook(io.BytesIO(client.get('/api/jobs/'+ident+'/export/xlsx').content))
 headers=[cell.value for cell in workbook['Опыт'][1]]
 assert 'fragment_id' in headers and 'context' in headers
 assert client.delete('/api/jobs/'+ident).status_code==200


def test_embedding_failure_is_explicit_and_does_not_call_provider(monkeypatch):
 class Fake:
  def json(self,*args):raise AssertionError('Provider must not be called when embeddings are unavailable')
 monkeypatch.setattr(app,'CheckedLLM',Fake)
 def unavailable(texts):raise OSError('private-detail')
 monkeypatch.setattr(app,'HybridRetriever',unavailable)
 with pytest.raises(app.AIError,match='embedding-модель') as caught:
  app.analyze({**PAYLOAD,'mode':'ai'})
 assert 'private-detail' not in str(caught.value)
