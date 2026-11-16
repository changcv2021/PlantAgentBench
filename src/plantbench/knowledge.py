"""Deterministic query-based retrieval from a frozen independent knowledge corpus."""
import collections
import functools
import hashlib
import json
import math
import re
from pathlib import Path
from .contracts import EvidenceUnit, Payload

STOP = {'the','and','with','that','this','from','what','which','for','are','was','have','has','can','how','should','plant','plants'}


def tokens(text):
    return [word for word in re.findall(r'[a-z0-9]+',text.casefold()) if len(word)>2 and word not in STOP]


@functools.lru_cache(maxsize=4)
def load_corpus(path, digest):
    import os
    root = Path(os.environ['PAB_RELEASE_ROOT']).resolve()
    filename = root / 'knowledge/knowledge_corpus.json'  # HF cache symlinks; exact digest checked below
    if not filename.is_relative_to(root) or filename.stat().st_size > 16*1024*1024:
        raise ValueError('KNOWLEDGE_CORPUS_OUTSIDE_ALLOWED_STORAGE_OR_SIZE')
    data = filename.read_bytes()
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('KNOWLEDGE_CORPUS_HASH_MISMATCH')
    docs = json.loads(data)['documents']
    if len({doc['id'] for doc in docs}) != len(docs):
        raise ValueError('DUPLICATE_KNOWLEDGE_ID')
    bags = [collections.Counter(tokens(doc['title']+' '+doc['text'])) for doc in docs]
    frequencies = collections.Counter(word for bag in bags for word in bag)
    return docs, bags, frequencies


class KnowledgeResolver:
    def __init__(self, spec):
        self.spec = spec
        self.docs, self.bags, self.frequencies = load_corpus(spec.path, spec.sha256)

    def unit(self, doc):
        return frozen_unit(self.spec.namespace,doc['id'],doc['title'],doc['text'],tuple(doc['provenance_refs']))

    def search(self, query, top_k):
        return [self.unit(self.docs[index]) for index in search_indices(self.spec.path,self.spec.sha256,query,top_k)]

    def all_units(self):
        return [self.unit(doc) for doc in self.docs]


@functools.lru_cache(maxsize=4096)
def search_indices(path,digest,query,top_k):
        # Only corpus identity and the public query affect retrieval. Cache
        # immutable positions, never mutable evidence or case/gold state.
        docs,bags,frequencies=load_corpus(path,digest)
        terms=set(tokens(query)); size=len(docs)
        scored=[]
        for index, bag in enumerate(bags):
            length=max(1,sum(bag.values()))
            score=sum(math.log(1+size/(1+frequencies[t])) * bag[t]/(bag[t]+0.5+length/500)
                      for t in terms if bag[t])
            title_terms=tokens(docs[index]['title'])
            score+=sum(2*math.log(1+size/(1+frequencies[t])) for t in terms & set(title_terms))
            if tokens(query)==title_terms and title_terms:score+=1000
            if score>0:
                scored.append((-score,docs[index]['id'],index))
        return tuple(index for _,_,index in sorted(scored)[:top_k])


@functools.lru_cache(maxsize=2048)
def frozen_unit(namespace,doc_id,title,content,provenance_refs):
        # Environment and wire always copy payloads before adding media bytes;
        # this cached source contract is never mutated by a case or by gold.
        ident = hashlib.sha256((namespace+'\0'+doc_id).encode()).hexdigest()[:24]
        return EvidenceUnit(evidence_uid='kb_'+ident, public_evidence_id='e_k'+ident,
            canonical_refs=[doc_id], provenance_refs=list(provenance_refs),
            subject_scope='knowledge_entry', subject_refs=[doc_id], variable_id=None,
            payload=Payload(kind='text',value=title+'\n'+content),
            measurement_method='source_knowledge_document', evidence_kind='general_knowledge',
            evidence_time_policy='curated_record_review', release_phase=0,
            access_class='observation_candidate', permission_status='allowed', quality_status='accepted',
            semantic_atom_ids=['knowledge:'+doc_id], text_role='independent_general_knowledge')
