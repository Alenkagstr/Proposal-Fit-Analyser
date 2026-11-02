import pytest
from retrieval import HybridRetriever, fragments
from retrieval_match import match_retrieved
import notebook_core as core


class Encoder:
    """Controlled multilingual vectors isolate fusion from model downloading."""
    def passage_embed(self,texts,**kwargs):
        for text in texts:
            lower=text.lower()
            yield [1.,0.,0.] if any(w in lower for w in ('genetic','генет','gwas')) else [0.,1.,0.] if 'astronom' in lower else [0.,0.,1.]
    query_embed=passage_embed


def req(quote='genetic association analysis'):
    return dict(id='R1',summary_en='genetic association analysis',quote=quote,type='метод',importance='ключевое',stated='прямо')


def test_fragment_offsets_cover_every_character_and_preserve_sources():
    text=('Первый проект и его результаты.\n\n'+ 'Длинное описание научного проекта '*20+'\n\nМетод GWAS применён в исследовании.')
    chunks=fragments({'CV':text,'заявка':'Другой документ.','идентификаторы':'not evidence'},limit=180,overlap=40)
    covered=set()
    for chunk in chunks:
        assert chunk['text']==({'CV':text,'заявка':'Другой документ.'}[chunk['source']])[chunk['start']:chunk['end']]
        assert len(chunk['text'])<=180
        if chunk['source']=='CV':covered.update(range(chunk['start'],chunk['end']))
    assert all(i in covered or c.isspace() for i,c in enumerate(text))
    assert any('GWAS' in c['text'] for c in chunks)
    assert not any(c['source']=='идентификаторы' for c in chunks)


def test_semantic_recall_across_languages_without_shared_words():
    search=HybridRetriever({'CV':'Изучала генетические связи заболеваний.','заявка':'Astronomy and galaxies.','extra':'Управление закупками.'},encoder=Encoder())
    found=search.search_many([req()],top_k=1)['R1']
    assert found[0]['source']=='CV' and found[0]['bm25_score']==0


def test_bm25_rescues_exact_scientific_abbreviation():
    class Flat:
        def passage_embed(self,texts,**kwargs):return [[1.,1.] for t in texts]
        query_embed=passage_embed
    search=HybridRetriever({'CV':'Работа с данными.','заявка':'Проводила PheWAS с контролем ковариат.'},encoder=Flat())
    found=search.search_many([req('PheWAS')],top_k=1)['R1']
    assert found[0]['source']=='заявка' and found[0]['bm25_score']>0


def test_invalid_vectors_are_not_silently_replaced_by_keyword_search():
    class Broken:
        def passage_embed(self,texts,**kwargs):return [[float('nan')]]
    with pytest.raises(ValueError,match='embedding'):
        HybridRetriever({'CV':'Научные данные'},encoder=Broken())


def results(text='Применяла GWAS в завершённом исследовании.'):
    search=HybridRetriever({'CV':text},encoder=Encoder())
    return search.search_many([req()])


@pytest.mark.parametrize('strength,expected',[('проект',core.STATUSES[0]),('упоминание',core.STATUSES[1])])
def test_retrieved_source_becomes_grounded_evidence(strength,expected):
    retrieved=results()
    class LLM:
        def json(self,system,user,schema,name):
            assert 'Применяла GWAS' in user
            return {'matches':[dict(req_id='R1',status=core.STATUSES[0],evidence_ids=[],rationale='Применён метод.',question='',supports=[dict(fragment_id='F1',quote='Применяла GWAS в завершённом исследовании.',strength=strength,type='проект',summary_en='GWAS project')])]}
    matrix,evidence=match_retrieved(LLM(),[req()],[],retrieved,reserved_ids={'E1'})
    assert matrix[0]['status']==expected
    assert evidence[0]['id']=='E2'
    assert matrix[0]['evidence_ids']==['E2']
    assert evidence[0]['context']==retrieved['R1'][0]['text']
    assert evidence[0]['source_start']==0


@pytest.mark.parametrize('fragment,quote',[('F999','Применяла GWAS'),('F1','Выполнила выдуманную работу')])
def test_unknown_fragment_or_invented_quote_cannot_confirm_experience(fragment,quote):
    class LLM:
        def json(self,*args):return {'matches':[dict(req_id='R1',status=core.STATUSES[0],evidence_ids=[],rationale='Claim',question='',supports=[dict(fragment_id=fragment,quote=quote,strength='проект',type='проект',summary_en='Claim')])]}
    matrix,evidence=match_retrieved(LLM(),[req()],[],results())
    assert not evidence
    assert matrix[0]['status']==core.STATUSES[3]
    assert not matrix[0]['evidence_ids']


def test_context_includes_negation_and_future_intent():
    text='Планирую применить GWAS в будущем; в завершённых проектах метод не использовала.'
    class LLM:
        def json(self,system,user,*args):
            assert text in user and 'отрицания' in system
            return {'matches':[dict(req_id='R1',status=core.STATUSES[1],evidence_ids=[],rationale='План, не выполненная работа.',question='Приведите пример выполненной работы.',supports=[dict(fragment_id='F1',quote=text,strength='упоминание',type='метод',summary_en='Intention')])]}
    matrix,evidence=match_retrieved(LLM(),[req()],[],results(text))
    assert matrix[0]['status']==core.STATUSES[1]
    assert evidence[0]['quote']==text


def test_cached_model_is_loaded_without_network(monkeypatch,tmp_path):
 import retrieval,sys
 from types import SimpleNamespace as N
 calls=[]
 def load(**kwargs):
  calls.append(kwargs)
  assert kwargs.get('local_files_only') is True
  return 'cached-model'
 monkeypatch.setattr(retrieval,'CACHE',tmp_path)
 monkeypatch.setitem(sys.modules,'fastembed',N(TextEmbedding=load))
 retrieval.embedding_model.cache_clear()
 try:assert retrieval.embedding_model()=='cached-model'
 finally:retrieval.embedding_model.cache_clear()
 assert len(calls)==1


def test_parallel_batches_keep_all_context_without_repeating_shared_fragments():
 import json,threading
 barrier=threading.Barrier(2)
 requests=[]
 requirements=[{**req(),'id':f'R{i}'} for i in range(1,9)]
 shared=results()['R1']
 retrieved={r['id']:shared for r in requirements}
 class LLM:
  def json(self,system,user,*args):
   data=json.loads(user.split('ФРАГМЕНТЫ ПО ТРЕБОВАНИЯМ:\n')[1])
   assert len(data['fragments'])==1
   assert len(data['candidates'])==4
   assert data['fragments'][0]['text']==shared[0]['text']
   requests.append(data)
   barrier.wait(timeout=5)
   return {'matches':[dict(req_id=rid,status=core.STATUSES[2],evidence_ids=[],supports=[],rationale='Нет подходящего опыта.',question='Есть ли пример работы?',confirmed_part='',missing_part='Нет примера работы.') for rid in data['candidates']]}
 matrix,evidence=match_retrieved(LLM(),requirements,[],retrieved)
 assert len(requests)==2 and not evidence
 assert [m['req_id'] for m in matrix]==[r['id'] for r in requirements]
 assert all(len(m['retrieval'])==1 for m in matrix)


@pytest.mark.parametrize('gap,expected',[('Не описан этап независимой валидации.',core.STATUSES[1]),('',core.STATUSES[3])])
def test_partial_project_evidence_requires_a_specific_gap(gap,expected):
 class LLM:
  def json(self,*args):return {'matches':[dict(req_id='R1',status=core.STATUSES[1],evidence_ids=[],rationale='Применён метод без полного цикла.',question='',confirmed_part='Выполнен GWAS анализ.',missing_part=gap,supports=[dict(fragment_id='F1',quote='Применяла GWAS в завершённом исследовании.',strength='проект',type='проект',summary_en='GWAS')])]}
 matrix,evidence=match_retrieved(LLM(),[req()],[],results())
 assert matrix[0]['status']==expected
 if gap:
  assert matrix[0]['confirmed_part']=='Выполнен GWAS анализ.'
  assert gap in matrix[0]['question']
