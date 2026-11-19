from __future__ import annotations
import time
from dataclasses import dataclass,asdict,replace
from pathlib import Path
from typing import Any
from .adapter import ASRAdapter,Segment
from .chunker import fmt_ts,text_hash
from .config import RRF_K,SEMANTIC_MIN_SIMILARITY
from .db import RetrievalDB
from .embed import EmbedClient

@dataclass(frozen=True)
class RankingProfile:
    name:str='v031-and085-soft92'
    vector_weight:float=1.0
    phrase_weight:float=1.0
    and_weight:float=0.85
    or_weight:float=0.08
    support_bonus_per_extra:float=0.006
    support_bonus_cap:float=0.018
    source_aggregate_bonus_cap:float=0.020
    source_diversity_penalty:float=0.92
    temporal_duplicate_overlap:float=0.25

    def lexical_weight(self,variant:str|None)->float:
        return {'phrase':self.phrase_weight,'and':self.and_weight,'or':self.or_weight}.get(str(variant),0.0)

    def public(self)->dict[str,Any]: return asdict(self)

@dataclass
class CandidateClip:
    source_id:str; title:str; source_path:str; start:float; end:float
    segment_start_id:int; segment_end_id:int; text:str; score:float; reason:str
    supporting_chunk_ids:list[int]; channels:list[str]; max_vector_similarity:float|None; best_lexical_rank:int|None
    base_score:float=0.0; strongest_rrf_score:float=0.0; support_bonus:float=0.0; source_aggregate_bonus:float=0.0; source_diversity_penalty:float=1.0
    sequence:int|None=None; provider:str=''; subject:str=''; course_year:str=''; source_url:str|None=None; source_urls:tuple[str,...]=()
    def public(self)->dict[str,Any]:
        d=asdict(self); d['start_hms']=fmt_ts(self.start); d['end_hms']=fmt_ts(self.end); return d

class SearchEngine:
    def __init__(self,db_path:str|Path,embedder:EmbedClient,semantic_min_similarity:float=SEMANTIC_MIN_SIMILARITY,ranking_profile:RankingProfile|None=None):
        self.db=RetrievalDB(db_path); self.embedder=embedder; self.semantic_min_similarity=semantic_min_similarity; self.profile=ranking_profile or RankingProfile()
    def close(self):self.db.close()

    def _rrf(self,lexical:list[dict[str,Any]],vector:list[dict[str,Any]],limit:int=36)->list[dict[str,Any]]:
        fused={}
        # Lexical tiers are intentionally asymmetric. OR remains candidate-generation evidence,
        # but contributes only a tiny fusion weight and never qualifies a hit by itself.
        for rank,h in enumerate(lexical,1):
            cid=int(h['id']); variant=h.get('lexical_variant'); weight=self.profile.lexical_weight(variant)
            x=fused.setdefault(cid,{**h,'rrf_score':0.0,'channels':set(),'lexical_rank':None,'lexical_variant':variant,'lexical_weight_used':None,'vector_rank':None,'vector_similarity':None})
            x['rrf_score']+=weight/(RRF_K+rank); x['channels'].add('lexical_candidate')
            x['lexical_rank']=rank; x['lexical_variant']=variant; x['lexical_weight_used']=weight
        for rank,h in enumerate(vector,1):
            cid=int(h['id'])
            x=fused.setdefault(cid,{**h,'rrf_score':0.0,'channels':set(),'lexical_rank':None,'lexical_variant':None,'lexical_weight_used':None,'vector_rank':None,'vector_similarity':None})
            x['rrf_score']+=self.profile.vector_weight/(RRF_K+rank); x['channels'].add('vector_candidate')
            x['vector_rank']=rank; x['vector_similarity']=h.get('vector_similarity')
        rows=sorted(fused.values(),key=lambda x:(-x['rrf_score'],x['id'])); accepted=[]
        for x in rows:
            semantic_ok=float(x.get('vector_similarity') or -1)>=self.semantic_min_similarity
            tier=x.get('lexical_variant'); lexical_ok=tier in ('phrase','and') and x.get('lexical_rank') is not None
            if lexical_ok or semantic_ok:
                qualified=[]
                if lexical_ok: qualified.append('lexical')
                if semantic_ok: qualified.append('vector')
                x['channels']=qualified; x['lexical_tier']=tier if lexical_ok else None; accepted.append(x)
            if len(accepted)>=limit:break
        return accepted

    @staticmethod
    def _load_segments(path:str)->list[Segment]:return list(ASRAdapter.load(path).segments)
    @staticmethod
    def _segment_window(segs:list[Segment],start_id:int,end_id:int,padding_s:float)->tuple[int,int,float,float,str]:
        by_id={s.id:i for i,s in enumerate(segs)}
        if start_id not in by_id or end_id not in by_id:raise RuntimeError('roundtrip segment id not found')
        a,b=by_id[start_id],by_id[end_id];want_start=max(0.0,segs[a].start-padding_s);want_end=segs[b].end+padding_s
        while a>0 and segs[a-1].end>=want_start:a-=1
        while b+1<len(segs) and segs[b+1].start<=want_end:b+=1
        selected=segs[a:b+1]
        return selected[0].id,selected[-1].id,selected[0].start,selected[-1].end,' '.join(s.text for s in selected)

    def _max_theoretical_rrf(self)->float:
        return (self.profile.vector_weight+max(self.profile.phrase_weight,self.profile.and_weight,self.profile.or_weight))/(RRF_K+1)

    def _candidate_clips(self,hits:list[dict[str,Any]],max_clips:int,padding_s:float=10.0,merge_gap_s:float=20.0,max_clip_s:float=300.0)->tuple[list[CandidateClip],list[CandidateClip]]:
        if not hits:return [],[]
        by_doc={}
        for h in hits:by_doc.setdefault(h['document_id'],[]).append(h)
        theoretical=max(1e-12,self._max_theoretical_rrf())
        # Source aggregate is a small bounded feature only; it never selects/rejects a source.
        source_strength={}
        for doc_id,rows in by_doc.items():
            vals=sorted((float(x['rrf_score']) for x in rows),reverse=True)[:3]
            source_strength[doc_id]=min(1.0,sum(vals)/(3.0*theoretical)) if vals else 0.0
        candidates=[]
        for doc_id,rows in by_doc.items():
            rows=sorted(rows,key=lambda h:(h['time_start'],h['time_end']));groups=[];cur=[]
            for h in rows:
                if not cur:cur=[h];continue
                gstart=min(x['time_start'] for x in cur);gend=max(x['time_end'] for x in cur);union_end=max(gend,h['time_end'])
                if h['time_start']<=gend+merge_gap_s and union_end-gstart<=max_clip_s:cur.append(h)
                else:groups.append(cur);cur=[h]
            if cur:groups.append(cur)
            doc=ASRAdapter.load(rows[0]['source_path']); segs=list(doc.segments)
            source_urls=tuple(str(x) for x in (doc.metadata.get('source_urls') or []) if str(x).strip())
            if not source_urls and doc.metadata.get('downloaded_from'):source_urls=(str(doc.metadata['downloaded_from']),)
            for g in groups:
                start_id=min(int(x['segment_start_id']) for x in g);end_id=max(int(x['segment_end_id']) for x in g)
                sid0,sid1,start,end,text=self._segment_window(segs,start_id,end_id,padding_s)
                channels=sorted({c for x in g for c in x['channels']});sims=[float(x['vector_similarity']) for x in g if x.get('vector_similarity') is not None];lex=[int(x['lexical_rank']) for x in g if x.get('lexical_rank') is not None]
                strongest=max(float(x['rrf_score']) for x in g); strongest_norm=min(1.0,strongest/theoretical)
                support_bonus=min(self.profile.support_bonus_cap,self.profile.support_bonus_per_extra*max(0,len(g)-1))
                source_bonus=self.profile.source_aggregate_bonus_cap*source_strength.get(doc_id,0.0)
                base=min(1.0,strongest_norm+support_bonus+source_bonus)
                lex_tiers={x.get('lexical_tier') for x in g if x.get('lexical_tier')}
                if 'lexical' in channels and 'vector' in channels:why='lexical + semantic agreement'
                elif 'lexical' in channels:why='lexical match'
                else:why='semantic paraphrase match'
                if lex_tiers:why+=f"; lexical tier={sorted(lex_tiers)[0]}"
                if len(g)>1:why+=f'; {len(g)} nearby supporting chunks merged (bonus capped)'
                candidates.append(CandidateClip(doc_id,g[0]['title'],g[0]['source_path'],start,end,sid0,sid1,text,round(base,6),why,[int(x['id']) for x in sorted(g,key=lambda z:-z['rrf_score'])],channels,max(sims) if sims else None,min(lex) if lex else None,round(base,6),strongest,round(support_bonus,6),round(source_bonus,6),1.0,doc.sequence,doc.provider,doc.subject,doc.course_year,source_urls[0] if source_urls else None,source_urls))
        candidates.sort(key=lambda c:(-c.base_score,-(c.max_vector_similarity or -1),c.start))
        # Temporal dedupe removes only substantially overlapping regions from the same source.
        temporal=[]
        for c in candidates:
            duplicate=False
            for k in temporal:
                if c.source_id!=k.source_id:continue
                inter=max(0.0,min(c.end,k.end)-max(c.start,k.start));shorter=max(1e-9,min(c.end-c.start,k.end-k.start))
                if inter/shorter>=self.profile.temporal_duplicate_overlap:duplicate=True;break
            if not duplicate:temporal.append(c)
        # Soft source diversity: repeated source is penalized, not forbidden. Distinct regions can survive.
        selected=[]; remaining=list(temporal); source_counts={}
        while remaining and len(selected)<max_clips:
            scored=[]
            for c in remaining:
                count=source_counts.get(c.source_id,0); penalty=self.profile.source_diversity_penalty**count; adjusted=c.base_score*penalty
                scored.append((adjusted,c.max_vector_similarity or -1.0,c.base_score,-c.start,c,penalty))
            _,_,_,_,best,penalty=max(scored,key=lambda x:(x[0],x[1],x[2],x[3]))
            selected.append(replace(best,score=round(best.base_score*penalty,6),source_diversity_penalty=round(penalty,6)))
            source_counts[best.source_id]=source_counts.get(best.source_id,0)+1
            remaining.remove(best)
        return selected,temporal

    @staticmethod
    def _debug_hit(h:dict[str,Any])->dict[str,Any]:
        keep=('id','document_id','ordinal','segment_start_id','segment_end_id','time_start','time_end','title','provider','subject','course_year','sequence','bm25','lexical_rank','lexical_variant','lexical_weight_used','lexical_tier','vector_rank','vector_similarity','rrf_score','channels')
        return {k:h.get(k) for k in keep if k in h}
    def search(self,query:str,max_clips:int=3,filters:dict[str,Any]|None=None,debug:bool=False)->dict[str,Any]:
        t0=time.perf_counter();filters=dict(filters or {})
        t=time.perf_counter();lexical,lex_mode=self.db.lexical(query,filters=filters);lex_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();qvec=self.embedder.embed_query(query);emb_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();vector,vector_mode=self.db.vector(qvec,filters=filters);vec_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();fused=self._rrf(lexical,vector,limit=36);fuse_ms=(time.perf_counter()-t)*1000
        t=time.perf_counter();clips,raw_clips=self._candidate_clips(fused,max(1,min(3,int(max_clips))));clip_ms=(time.perf_counter()-t)*1000
        unique_sources=len({c.source_id for c in clips});source_diversity=(unique_sources/len(clips)) if clips else 1.0
        out={'query':query,'filters':filters,'clips':[c.public() for c in clips],'diagnostics':{
            'index_generation':self.db.meta('generation',0),'embedding_identity':self.db.meta('embedding_identity',{}),'rrf_k':RRF_K,'semantic_min_similarity':self.semantic_min_similarity,'ranking_profile':self.profile.public(),
            'retrievers':{'lexical':{'ok':True,'mode':lex_mode,'ms':round(lex_ms,2),'count':len(lexical)},'vector':{'ok':True,'mode':vector_mode,'ms':round(vec_ms,2),'count':len(vector),'query_embedding_ms':round(emb_ms,2)}},
            'fusion':{'mode':'tiered_weighted_rrf','vector_weight':self.profile.vector_weight,'lexical_weights':{'phrase':self.profile.phrase_weight,'and':self.profile.and_weight,'or':self.profile.or_weight},'accepted_chunks':len(fused),'ms':round(fuse_ms,2)},
            'candidate_clip':{'count':len(clips),'raw_candidate_count':len(raw_clips),'padding_seconds':10.0,'merge_gap_seconds':20.0,'max_clip_seconds':300.0,'source_diversity_ratio':round(source_diversity,3),'source_diversity_mode':'soft_penalty','temporal_duplicate_overlap':self.profile.temporal_duplicate_overlap,'ms':round(clip_ms,2)},'degraded':False,'warnings':[],'total_ms':round((time.perf_counter()-t0)*1000,2)}}
        if debug:out['_debug']={'lexical':[self._debug_hit(x) for x in lexical],'vector':[self._debug_hit(x) for x in vector],'fused':[self._debug_hit(x) for x in fused],'clips':[c.public() for c in raw_clips]}
        return out
    def roundtrip_clip(self,clip:dict[str,Any])->dict[str,Any]:
        doc=ASRAdapter.load(clip['source_path']);selected=[s for s in doc.segments if int(clip['segment_start_id'])<=s.id<=int(clip['segment_end_id'])]
        if not selected:raise RuntimeError('clip roundtrip empty')
        reconstructed=' '.join(s.text for s in selected);ok=(abs(selected[0].start-float(clip['start']))<1e-6 and abs(selected[-1].end-float(clip['end']))<1e-6 and reconstructed==clip['text'])
        return {'ok':ok,'source_id':doc.source_id,'segment_count':len(selected),'segment_start_id':selected[0].id,'segment_end_id':selected[-1].id,'start':selected[0].start,'end':selected[-1].end,'text_sha256':text_hash(reconstructed)}
