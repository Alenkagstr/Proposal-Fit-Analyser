"""Real-model smoke check; requires downloaded weights/Hugging Face access.
Run from repository root: .venv/bin/python tests/embedding_smoke.py
"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from retrieval import HybridRetriever,MODEL
texts={'CV':'Разрабатывала методы машинного обучения и обучала нейронные сети для медицинских изображений.',
       'заявка':'Проводила археологические раскопки и изучала древнюю керамику.',
       'дополнительные материалы':'Руководила закупками и бухгалтерской отчётностью.'}
r=HybridRetriever(texts)
found=r.search_many([{'id':'R1','summary_en':'Train deep learning models for medical image analysis','quote':'Develop neural networks for radiology'}],top_k=1)['R1']
assert found[0]['source']=='CV',found
assert len(r.vectors[0])==384
print(f'Real multilingual embedding smoke passed: {MODEL}; 384 dimensions; English query retrieves Russian medical experience.')
