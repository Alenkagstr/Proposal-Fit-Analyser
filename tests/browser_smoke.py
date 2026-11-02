from playwright.sync_api import sync_playwright
from pathlib import Path
import os
out=Path(__file__).resolve().parents[1]/'.test-artifacts'
out.mkdir(exist_ok=True)
with sync_playwright() as p:
 browser=p.chromium.launch(executable_path=os.getenv('CHROMIUM_PATH','/usr/bin/chromium'),headless=True,args=['--no-sandbox'])
 page=browser.new_page(viewport={'width':1440,'height':1080},device_scale_factor=1)
 errors=[];page.on('pageerror',lambda e: errors.append(str(e)))
 page.goto(os.getenv('APP_URL','http://127.0.0.1:8000'));page.wait_for_selector('#demo')
 page.screenshot(path=str(out/'desktop.png'),full_page=True)
 page.click('#demo');page.fill('#application','Научный проект: медицинские изображения на Python и PyTorch. https://example.org/publications');page.click('#run');page.wait_for_selector('#result-view:not([hidden])')
 local_result=page.evaluate('result')
 assert page.locator('#matrix-body tr').count()==6
 page.locator('#matrix-body details').first.locator('summary').click()
 assert page.locator('blockquote').first.is_visible()
 page.select_option('#filter','не найдено в материалах')
 assert page.locator('#matrix-body tr').count()>0
 page.click('[data-tab="risks"]');assert page.locator('.questions li').count()==6
 page.click('[data-tab="sources"]');assert page.locator('#sources-panel a').count()==1
 page.click('[data-tab="report"]');assert 'Аналитическая справка' in page.locator('#report-text').inner_text()
 page.click('[data-tab="matrix"]');page.select_option('#filter','all')
 with page.expect_download() as dl: page.click('#export-xlsx')
 assert dl.value.suggested_filename.endswith('.xlsx')
 page.screenshot(path=str(out/'result.png'),full_page=True)
 page.set_viewport_size({'width':390,'height':844});page.click('#back')
 assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
 page.screenshot(path=str(out/'mobile.png'),full_page=True)
 page.select_option('#mode','ai');assert page.locator('#ai-settings').get_attribute('open') is not None
 page.route('**/api/ai/connect',lambda route: route.fulfill(json={'models':['test-model'],'model':'test-model','message':'Ключ принят'}))
 page.route('**/api/ai/check',lambda route: route.fulfill(json={'ok':True,'model':'test-model','message':'Проверка прошла'}))
 page.fill('#ai-key','browser-test-key');page.click('#ai-connect');page.wait_for_function("document.querySelector('#ai-model').value === 'test-model'")
 assert page.locator('#ai-key').input_value()==''
 page.click('#ai-check');page.wait_for_function("document.querySelector('#ai-status').textContent.includes('AI проверен')")
 page.select_option('#mode','local')
 page.set_input_files('#cv-file',{'name':'cv.txt','mimeType':'text/plain','buffer':'Python: разработка моделей анализа научных данных.'.encode()})
 page.wait_for_function("document.querySelector('#cv').value.startsWith('Python:')")
 page.click('#nav-result') if page.locator('#nav-result').is_visible() else page.evaluate("document.querySelector('#nav-result').click()")
 page.click('#delete');page.wait_for_function("document.querySelector('#cv').value === ''")
 # Fail centrally, retry without losing any document, then return to edit.
 page.click('#demo');page.fill('#application','Заявка: научный проект Python, исходные данные сохранены.')
 original=page.locator('#cv').input_value()
 page.route('**/api/analyze',lambda route: route.fulfill(status=202,json={'id':'mock-failed'}))
 page.route('**/api/jobs/mock-failed',lambda route: route.fulfill(json={'status':'error','stage':'Извлечение опыта CV','failed_stage':'Извлечение опыта CV','error':'Neural Deep не ответил вовремя.'}))
 page.click('#run');page.wait_for_selector('#progress-failure:not([hidden])')
 assert page.locator('#progress-error').is_visible()
 assert page.locator('#progress-error').inner_text()=='Neural Deep не ответил за отведённое время запроса.'
 assert page.locator('#stage').is_hidden()
 assert page.locator('#progress-spinner').is_hidden()
 page.click('#retry-analysis');page.wait_for_selector('#progress-failure:not([hidden])')
 page.screenshot(path=str(out/'error-mobile.png'),full_page=True)
 page.click('#progress-back')
 assert page.locator('#cv').input_value()==original
 assert 'исходные данные сохранены' in page.locator('#application').input_value()
 # A failed poll resumes the existing job instead of submitting it again.
 page.route('**/api/jobs/mock-failed',lambda route: route.abort())
 page.click('#run');page.wait_for_selector('#progress-failure:not([hidden])')
 page.unroute('**/api/analyze')
 page.route('**/api/analyze',lambda route: (_ for _ in ()).throw(AssertionError('duplicate analysis')))
 page.click('#retry-analysis');page.wait_for_selector('#progress-failure:not([hidden])')
 page.click('#progress-back')
 page.clock.install()
 page.evaluate("""() => {
  const originalFetch=window.fetch;
  window.fetch=(url,options={})=>String(url).includes('/api/jobs/mock-failed')
   ? new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}))
   : originalFetch(url,options);
 }""")
 page.unroute('**/api/analyze')
 page.route('**/api/analyze',lambda route: route.fulfill(status=202,json={'id':'mock-failed'}))
 page.click('#run')
 page.wait_for_timeout(200)
 page.clock.run_for(15001)
 page.wait_for_selector('#progress-failure:not([hidden])')
 assert 'не ответил вовремя' in page.locator('#progress-error').inner_text()
 assert page.locator('#cv').input_value()==original
 page.click('#progress-back')
 page.locator('#ai-settings').evaluate('(element)=>element.open=true')
 assert page.locator('#ai-model').evaluate('(element)=>element.tagName')=='SELECT'
 assert page.locator('#ai-load-models').count()==0
 page.screenshot(path=str(out/'ai-settings-mobile.png'),full_page=True)
 page.evaluate("""() => {
  const originalFetch=window.fetch;
  window.fetch=(url,options={})=>String(url).includes('/api/ai/check')
   ? new Promise((resolve,reject)=>options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true}))
   : originalFetch(url,options);
 }""")
 page.click('#ai-check')
 page.clock.run_for(45001)
 page.wait_for_function("document.querySelector('#ai-message').textContent.includes('Сервер не ответил вовремя')")
 assert page.locator('#ai-check').is_enabled()
 assert page.locator('#cv').input_value()==original
 # Render source context returned by the hybrid pipeline.
 page.clock.resume()
 local_result['mode']='ai'
 local_result['matrix'][0].update(status='частично подтверждено',confirmed_part='Подтверждён анализ данных.',missing_part='Не описана независимая валидация.')
 for evidence in local_result['evidence']:evidence.update(context='Контекст исходного научного проекта.',fragment_id='F1',source_start=0,source_end=36)
 page.route('**/api/analyze',lambda route:route.fulfill(status=202,json={'id':'hybrid-ui'}))
 page.route('**/api/jobs/hybrid-ui',lambda route:route.fulfill(json={'status':'done','stage':'Готово','result':local_result}))
 page.select_option('#mode','local')
 page.click('#run')
 page.wait_for_selector('#result-view:not([hidden])')
 page.locator('#matrix-body details').first.locator('summary').first.click()
 assert page.locator('#analysis-notice').is_hidden()
 assert 'Что подтверждено:' in page.locator('#matrix-body').inner_text()
 assert 'Не описана независимая валидация.' in page.locator('#matrix-body').inner_text()
 context=page.locator('#matrix-body blockquote details').first
 context.locator('summary').click()
 assert 'Контекст исходного научного проекта.' in context.inner_text()
 assert not errors, errors
 print('Browser checks passed: demo, 6 rows, quotes, filters, risks/questions, report, XLSX download, upload, delete, 390px layout; central failure, retry, preserved documents and poll recovery; no JS errors; AI settings (mocked provider).')
 page.unroute_all(behavior='ignoreErrors')
 browser.close()
