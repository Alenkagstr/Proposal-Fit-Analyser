from quote_checks import locate_quote


def test_typography_and_pdf_ligatures():
    quote='Метод «two-sample MR» и scientific analysis'
    source='Метод “two‑sample MR” и scientiﬁc\u00a0analysis'
    assert locate_quote(quote,source)[1]=='normalized'


def test_expand_omission_preserves_intervening_negation():
    source='Разработал метод анализа данных, но не применял его самостоятельно, в рамках проекта исследования.'
    quote='Разработал метод анализа данных […] в рамках проекта исследования.'
    expanded,method=locate_quote(quote,source)
    assert method=='expanded_omission'
    assert 'не применял его самостоятельно' in expanded
    assert locate_quote(expanded,source)[1]=='normalized'


def test_no_fuzzy_rephrasing_wrong_order_or_short_fragments():
    assert locate_quote('Провёл GWAS анализ','Планирует провести GWAS анализ')[1]=='not_found'
    assert locate_quote('в рамках проекта исследования ... Разработал метод анализа данных',
                        'Разработал метод анализа данных в рамках проекта исследования')[1]=='not_found'
    assert locate_quote('Python ... R','Python и другие инструменты, в том числе R')[1]=='not_found'
