"""Small local vector index: TF-IDF features, cosine ranking, optional FAISS acceleration."""
import json, math, re
from collections import Counter
from pathlib import Path
from functools import lru_cache

def words(text): return re.findall(r'[a-z]+', text.lower())
@lru_cache(maxsize=1)
def index():
    docs=json.loads(Path(__file__).with_name('policies.json').read_text())
    counts=[Counter(words(d['intent']+' '+d['text'])) for d in docs]
    vocab=sorted(set().union(*(set(c) for c in counts)))
    idf={w:1+math.log((1+len(docs))/(1+sum(w in c for c in counts))) for w in vocab}
    def vector(text):
        c=Counter(words(text)); v=[c[w]*idf[w] for w in vocab]; norm=math.sqrt(sum(x*x for x in v)) or 1
        return [x/norm for x in v]
    matrix=[vector(d['intent']+' '+d['text']) for d in docs]
    try:
        import faiss, numpy as np
        idx=faiss.IndexFlatIP(len(vocab)); idx.add(np.asarray(matrix,dtype='float32'))
        return docs,vector,matrix,idx,np
    except ImportError:
        return docs,vector,matrix,None,None

def retrieve(query, k=3):
    docs,vector,matrix,idx,np=index(); q=vector(query)
    if idx is not None:
        scores,ids=idx.search(np.asarray([q],dtype='float32'),min(k,len(docs)))
        ranked=[(int(i),float(s)) for i,s in zip(ids[0],scores[0])]; backend='FAISS TF-IDF cosine'
    else:
        ranked=sorted(enumerate(sum(a*b for a,b in zip(q,v)) for v in matrix),key=lambda x:-x[1])[:k]; backend='Python TF-IDF cosine'
    return [dict(docs[i],score=round(s,3)) for i,s in ranked],backend

def for_intent(intent,message):
    hits,backend=retrieve((intent+' ')*8+message,3)
    # Explicit intent policy is mandatory; similarity alone is not an authorization rule.
    docs=index()[0]; primary=next((d for d in docs if d['intent']==intent),docs[6])
    return primary,hits,backend
