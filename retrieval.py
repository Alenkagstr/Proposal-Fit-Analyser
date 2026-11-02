"""Local multilingual dense retrieval + BM25 with reciprocal rank fusion.

Only model weights are cached on disk. Documents/vectors stay in the job's memory.
"""
from collections import Counter
from functools import lru_cache
import math
import os
from pathlib import Path
import re
import threading

MODEL='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
CACHE=Path(__file__).parent/'.model-cache'
_model_lock=threading.Lock()


@lru_cache(maxsize=1)
def embedding_model():
    from fastembed import TextEmbedding
    os.environ.setdefault('HF_HUB_DOWNLOAD_TIMEOUT','30')
    os.environ.setdefault('HF_HUB_ETAG_TIMEOUT','10')
    settings=dict(model_name=MODEL,cache_dir=str(CACHE),threads=4)
    if CACHE.exists():
        try:return TextEmbedding(**settings,local_files_only=True)
        except (ValueError,OSError):pass
    return TextEmbedding(**settings)


def fragments(texts,limit=900,overlap=150):
    """Prefer paragraph/sentence boundaries; retain exact offsets and overlapping context."""
    if limit<=overlap or overlap<0:raise ValueError('Invalid chunk limits')
    out=[]
    for source,text in texts.items():
        if source=='идентификаторы':continue
        start=0
        while start<len(text):
            end=min(start+limit,len(text))
            if end<len(text):
                boundaries=[m.end()+start for m in re.finditer(r'\n\s*\n|[.!?]\s+',text[start:end]) if m.end()>limit//2]
                if boundaries:end=boundaries[-1]
                else:
                    space=text.rfind(' ',start+limit//2,end)
                    if space>start:end=space
            left=start
            while left<end and text[left].isspace():left+=1
            right=end
            while right>left and text[right-1].isspace():right-=1
            if right>left:out.append(dict(id=f'F{len(out)+1}',source=source,start=left,end=right,text=text[left:right]))
            if end==len(text):break
            next_start=max(start+1,end-overlap)
            # Avoid beginning an overlap inside a word.
            while next_start<end and next_start>0 and text[next_start-1].isalnum() and text[next_start].isalnum():next_start+=1
            start=next_start
    return out


def terms(text):
    return re.findall(r'[^\W_]+(?:[-.][^\W_]+)*',text.casefold(),flags=re.UNICODE)


class HybridRetriever:
    def __init__(self,texts,encoder=None):
        self.fragments=fragments(texts)
        if not self.fragments:raise ValueError('Нет фрагментов для поиска.')
        self.encoder=encoder
        if self.encoder is None:
            with _model_lock:self.encoder=embedding_model()
        self.vectors=self._encode([f['text'] for f in self.fragments],False)
        self.counts=[Counter(terms(f['text'])) for f in self.fragments]
        self.lengths=[sum(c.values()) for c in self.counts]
        self.average=sum(self.lengths)/len(self.lengths) or 1
        self.df=Counter(t for c in self.counts for t in c)

    def _encode(self,texts,query):
        # FastEmbed tokenizers and ONNX sessions are shared between jobs; serialize inference.
        with _model_lock:
            values=list((self.encoder.query_embed if query else self.encoder.passage_embed)(texts,batch_size=32))
        if len(values)!=len(texts):raise ValueError('Некорректное число embedding-векторов.')
        normalized=[]
        for value in values:
            vector=[float(x) for x in value]
            norm=math.sqrt(sum(x*x for x in vector))
            if not vector or not math.isfinite(norm) or norm==0:raise ValueError('Некорректный embedding-вектор.')
            normalized.append([x/norm for x in vector])
        if len({len(v) for v in normalized})>1:raise ValueError('Несовместимые embedding-векторы.')
        return normalized

    def search_many(self,requirements,top_k=6):
        queries=[r['summary_en']+'\n'+r['quote'] for r in requirements]
        vectors=self._encode(queries,True)
        results={}
        for req,query,vector in zip(requirements,queries,vectors):
            if len(vector)!=len(self.vectors[0]):raise ValueError('Несовместимые embedding-векторы.')
            dense=[sum(a*b for a,b in zip(vector,v)) for v in self.vectors]
            lexical=[];n=len(self.fragments)
            for counts,length in zip(self.counts,self.lengths):
                score=0
                for term in set(terms(query)):
                    freq=counts[term]
                    if freq:
                        idf=math.log(1+(n-self.df[term]+.5)/(self.df[term]+.5))
                        score+=idf*freq*2.5/(freq+1.5*(.25+.75*length/self.average))
                lexical.append(score)
            ranks={}
            for scores,positive in [(dense,False),(lexical,True)]:
                order=sorted(range(n),key=lambda i:(-scores[i],i))
                if positive:order=[i for i in order if scores[i]>0]
                for rank,i in enumerate(order[:max(top_k*2,12)],1):ranks[i]=ranks.get(i,0)+1/(60+rank)
            selected=sorted(ranks,key=lambda i:(-ranks[i],i))[:top_k]
            results[req['id']]=[{**self.fragments[i],'semantic_score':round(dense[i],6),'bm25_score':round(lexical[i],6),'fusion_score':round(ranks[i],6)} for i in selected]
        return results


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='Download/cache local multilingual embedding model (no documents).')
    parser.add_argument('--prepare',action='store_true',required=True)
    parser.parse_args()
    model=embedding_model()
    vector=next(model.query_embed(['Проверка локальной модели поиска']))
    print(f'Embedding model ready: {MODEL}, dimensions={len(vector)}')
