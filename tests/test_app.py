import io,time
import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
import app

client=TestClient(app.app)
PAYLOAD=dict(cv='Python и PyTorch: проект сегментации медицинских изображений в 2024 году.\nСтатистический анализ результатов проекта.',proposal='Метод: сегментация медицинских изображений на Python и PyTorch.\nРесурс: доступ к лаборатории и GPU.\nЭтап: валидация за 12 месяцев.',mode='local',candidate='C01')

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
 assert wb.sheetnames==['Матрица','Требования','Опыт','Риски','Вопросы']
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
