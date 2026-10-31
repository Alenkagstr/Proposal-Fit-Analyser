"""Document helpers: retain hyperlink destinations and their source documents."""
import re
from urllib.parse import urlsplit
import notebook_core as core


def link_inventory(texts):
    found={}
    def add(url,kind,source):
        try:
            parsed=urlsplit(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password: return
        except ValueError: return
        key=url.rstrip('/')
        entry=found.setdefault(key,dict(url=url,type=kind,sources=[],status='Ссылка извлечена; содержимое не загружалось'))
        if source not in entry['sources']: entry['sources'].append(source)
    for kind,p in core.find_profiles(texts).items():
        for ident in [p['id']]+p.get('other_ids',[]):
            for source in p['sources']: add(core.PROFILE_URLS[kind].format(ident),kind,source)
    for source,text in texts.items():
        for raw in re.findall(core.URL_PATTERN,text,re.I):
            url=raw.rstrip('.,;:!?»”')
            if url.lower().startswith('www.'):url='https://'+url
            add(url,core.classify_link(url),source)
    # A normalized profile and its original URL can coincide. Prefer profile classification.
    return list(found.values())


def read_xlsx(path):
    from openpyxl import load_workbook
    wb=load_workbook(path,data_only=False)
    try:
        for sheet in wb:
            headers=[str(c.value or '') for c in next(sheet.iter_rows(),[])]
            if {'Кандидат','Поле','В заявке','В CV'}.issubset(headers):
                raise ValueError('Это сводка наличия полей по кандидатам, а не исходная заявка. Она не содержит значений и ссылок. Загрузите заявку одного кандидата отдельно от CV и Proposal.')
        parts=[]
        for sheet in wb:
            parts.append('Лист: '+sheet.title)
            for row in sheet:
                values=[]
                for cell in row:
                    if cell.value is not None: values.append(f'{cell.coordinate}: {cell.value}')
                    if cell.hyperlink and cell.hyperlink.target: values.append('Ссылка: '+cell.hyperlink.target)
                    if cell.data_type=='f':
                        m=re.match(r'=HYPERLINK\s*\(\s*"((?:[^"]|"")*)"',str(cell.value),re.I)
                        if m: values.append('Ссылка: '+m[1].replace('""','"'))
                if values: parts.append(' | '.join(values))
        return '\n'.join(parts)
    finally: wb.close()
