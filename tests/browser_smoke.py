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
 assert not errors, errors
 print('Browser checks passed: demo, 6 rows, quotes, filters, risks/questions, report, XLSX download, upload, delete, 390px layout; no JS errors; application links and AI settings flow (mocked provider).')
 browser.close()
