from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any
import libsql
from .adapter import CanonicalDocument
from .chunker import Chunk
from .config import SCHEMA_VERSION,CHUNKER_VERSION,EMBEDDING_MODEL,EMBEDDING_MODEL_SHA256,EMBEDDING_DIM,QUERY_INSTRUCTION,STOPWORDS,TOKEN_RE
from .embed import EmbedClient

SCHEMA_SQL=f'''
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,kind TEXT NOT NULL,root TEXT,config_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS documents(
 id TEXT PRIMARY KEY,source_id TEXT NOT NULL,path TEXT NOT NULL,type TEXT NOT NULL,title TEXT NOT NULL,
 provider TEXT,subject TEXT,course_year TEXT,sequence INTEGER,telegram_message_id INTEGER,source_variant TEXT,
 duration_seconds REAL NOT NULL,content_hash TEXT NOT NULL,metadata_json TEXT NOT NULL,indexed_generation INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS chunks(
 id INTEGER PRIMARY KEY,document_id TEXT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,ordinal INTEGER NOT NULL,
 segment_start_id INTEGER NOT NULL,segment_end_id INTEGER NOT NULL,time_start REAL NOT NULL,time_end REAL NOT NULL,
 text TEXT NOT NULL,embedding_text TEXT NOT NULL,text_hash TEXT NOT NULL,token_count INTEGER NOT NULL,chunker_version TEXT NOT NULL,
 UNIQUE(document_id,ordinal));
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(title,text);
CREATE TABLE IF NOT EXISTS chunk_embeddings(chunk_id INTEGER PRIMARY KEY,embedding F32_BLOB({EMBEDDING_DIM}) NOT NULL);
CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector ON chunk_embeddings(libsql_vector_idx(embedding,'metric=cosine'));
CREATE TABLE IF NOT EXISTS index_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS index_failures(document_id TEXT NOT NULL,stage TEXT NOT NULL,error TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL,PRIMARY KEY(document_id,stage));
CREATE INDEX IF NOT EXISTS idx_chunks_doc_time ON chunks(document_id,time_start,time_end);
CREATE INDEX IF NOT EXISTS idx_docs_subject_year ON documents(subject,course_year);
'''

def now_iso():
    from datetime import datetime,timezone
    return datetime.now(timezone.utc).isoformat()

class RetrievalDB:
    def __init__(self,path:str|Path):
        self.path=str(path); self.conn=libsql.connect(database=self.path); self.q=self.conn.cursor()
    def close(self): self.q.close(); self.conn.close()
    def init_schema(self):
        for stmt in [s.strip() for s in SCHEMA_SQL.split(';') if s.strip()]: self.q.execute(stmt)
        self.conn.commit()
    def meta(self,key:str,default:Any=None)->Any:
        self.q.execute('SELECT value FROM index_meta WHERE key=?',(key,)); row=self.q.fetchone()
        if not row:return default
        try:return json.loads(row[0])
        except Exception:return row[0]
    def set_meta(self,key:str,value:Any):
        self.q.execute('INSERT INTO index_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,json.dumps(value,ensure_ascii=False,sort_keys=True)))
    def next_generation(self)->int:return int(self.meta('generation',0))+1
    def _delete_document_rows(self,doc_id:str):
        self.q.execute('SELECT id FROM chunks WHERE document_id=?',(doc_id,)); ids=[int(r[0]) for r in self.q.fetchall()]
        for cid in ids:
            self.q.execute('DELETE FROM chunks_fts WHERE rowid=?',(cid,)); self.q.execute('DELETE FROM chunk_embeddings WHERE chunk_id=?',(cid,))
        self.q.execute('DELETE FROM chunks WHERE document_id=?',(doc_id,)); self.q.execute('DELETE FROM documents WHERE id=?',(doc_id,))
    def register_course(self,course_id:str,kind:str,root:str,config:dict[str,Any]):
        self.q.execute('INSERT INTO sources(id,kind,root,config_json) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,root=excluded.root,config_json=excluded.config_json',(course_id,kind,root,json.dumps(config,ensure_ascii=False,sort_keys=True)))
        self.conn.commit()
    def list_courses(self)->list[dict[str,Any]]:
        self.q.execute('SELECT s.id,s.kind,s.root,s.config_json,COUNT(DISTINCT d.id),COUNT(c.id),MIN(d.sequence),MAX(d.sequence) FROM sources s LEFT JOIN documents d ON d.source_id=s.id LEFT JOIN chunks c ON c.document_id=d.id GROUP BY s.id,s.kind,s.root,s.config_json ORDER BY s.id')
        out=[]
        for course_id,kind,root,config_json,documents,chunks,min_seq,max_seq in self.q.fetchall():
            try:config=json.loads(config_json)
            except Exception:config={'raw':config_json}
            out.append({'course_id':course_id,'kind':kind,'root':root,'config':config,'documents':int(documents or 0),'chunks':int(chunks or 0),'min_sequence':min_seq,'max_sequence':max_seq})
        return out
    def status(self)->dict[str,Any]:
        counts={}
        for name in ('documents','chunks','chunk_embeddings'):
            self.q.execute(f'SELECT COUNT(*) FROM {name}');counts[name]=int(self.q.fetchone()[0])
        return {'database':self.path,**counts,'generation':self.meta('generation',0),'embedding_identity':self.meta('embedding_identity',{}),'courses':self.list_courses()}
    def index_document(self,doc:CanonicalDocument,chunks:list[Chunk],embedder:EmbedClient)->dict[str,Any]:
        t0=time.perf_counter()
        embedding_identity={'model':EMBEDDING_MODEL,'model_sha256':EMBEDDING_MODEL_SHA256,'dimension':EMBEDDING_DIM,'metric':'cosine','query_instruction':QUERY_INSTRUCTION,'document_format':'Title/Subject/Course/Timestamp prefix + raw ASR segment text'}
        self.q.execute('SELECT content_hash,indexed_generation FROM documents WHERE id=?',(doc.source_id,)); existing=self.q.fetchone()
        if existing and existing[0]==doc.content_hash and self.meta('chunker',{}).get('version')==CHUNKER_VERSION and self.meta('embedding_identity',{})==embedding_identity:
            self.q.execute('SELECT COUNT(*) FROM chunks WHERE document_id=?',(doc.source_id,)); cc=int(self.q.fetchone()[0])
            self.q.execute('SELECT COUNT(*) FROM chunk_embeddings e JOIN chunks c ON c.id=e.chunk_id WHERE c.document_id=?',(doc.source_id,)); vc=int(self.q.fetchone()[0])
            if cc==len(chunks) and vc==len(chunks):
                return {'generation':int(existing[1]),'chunks':cc,'vectors':vc,'elapsed_ms':round((time.perf_counter()-t0)*1000,2),'skipped_unchanged':True}
        generation=self.next_generation(); embeddings=embedder.embed_documents([c.embedding_text for c in chunks])
        if len(embeddings)!=len(chunks):raise RuntimeError('embedding count mismatch')
        self.q.execute('BEGIN')
        try:
            self.q.execute('INSERT INTO sources(id,kind,root,config_json) VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,root=excluded.root',(doc.corpus,'canonical_asr_v2',str(Path(doc.source_path).parent),json.dumps({'schema_version':2})))
            self._delete_document_rows(doc.source_id)
            self.q.execute('INSERT INTO documents(id,source_id,path,type,title,provider,subject,course_year,sequence,telegram_message_id,source_variant,duration_seconds,content_hash,metadata_json,indexed_generation) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (doc.source_id,doc.corpus,doc.source_path,'asr_transcript',doc.title,doc.provider,doc.subject,doc.course_year,doc.sequence,doc.telegram_message_id,doc.source_variant,doc.duration_seconds,doc.content_hash,json.dumps(doc.metadata,ensure_ascii=False),generation))
            for c,emb in zip(chunks,embeddings):
                self.q.execute('INSERT INTO chunks(id,document_id,ordinal,segment_start_id,segment_end_id,time_start,time_end,text,embedding_text,text_hash,token_count,chunker_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (c.id,c.document_id,c.ordinal,c.segment_start_id,c.segment_end_id,c.time_start,c.time_end,c.text,c.embedding_text,c.text_hash,c.token_count,CHUNKER_VERSION))
                self.q.execute('INSERT INTO chunks_fts(rowid,title,text) VALUES(?,?,?)',(c.id,doc.title,c.text))
                self.q.execute('INSERT INTO chunk_embeddings(chunk_id,embedding) VALUES(?,vector(?))',(c.id,json.dumps(emb)))
            self.set_meta('schema_version',SCHEMA_VERSION); self.set_meta('generation',generation)
            self.set_meta('chunker',{'version':CHUNKER_VERSION,'target_tokens':420,'hard_max_tokens':620,'overlap_segments':1})
            self.set_meta('embedding_identity',embedding_identity)
            self.set_meta('indexed_at',now_iso()); self.conn.commit()
        except Exception:
            self.conn.rollback(); raise
        self.q.execute('SELECT COUNT(*) FROM chunks WHERE document_id=?',(doc.source_id,)); cc=int(self.q.fetchone()[0])
        self.q.execute('SELECT COUNT(*) FROM chunk_embeddings e JOIN chunks c ON c.id=e.chunk_id WHERE c.document_id=?',(doc.source_id,)); vc=int(self.q.fetchone()[0])
        if cc!=len(chunks) or vc!=len(chunks):raise RuntimeError('post-index verification failed')
        return {'generation':generation,'chunks':cc,'vectors':vc,'elapsed_ms':round((time.perf_counter()-t0)*1000,2)}
    def _fetch_chunk(self,cid:int)->dict[str,Any]:
        self.q.execute('SELECT c.id,c.document_id,c.ordinal,c.segment_start_id,c.segment_end_id,c.time_start,c.time_end,c.text,d.title,d.path,d.provider,d.subject,d.course_year,d.sequence FROM chunks c JOIN documents d ON d.id=c.document_id WHERE c.id=?',(cid,)); r=self.q.fetchone()
        if not r:raise KeyError(cid)
        keys=['id','document_id','ordinal','segment_start_id','segment_end_id','time_start','time_end','text','title','source_path','provider','subject','course_year','sequence']; return dict(zip(keys,r))
    @staticmethod
    def _filter_sql(filters:dict[str,Any]|None,alias:str='d')->tuple[str,list[Any]]:
        if not filters:return '',[]
        allowed={'provider':'provider','subject':'subject','course_year':'course_year','source_id':'id','sequence':'sequence','course_id':'source_id','course':'source_id'}
        clauses=[];params=[]
        for key,value in filters.items():
            if key not in allowed:raise ValueError(f'unsupported metadata filter: {key}')
            col=f'{alias}.{allowed[key]}'
            vals=value if isinstance(value,(list,tuple,set)) else [value]
            vals=list(vals)
            if not vals:clauses.append('1=0');continue
            clauses.append(col+' IN ('+','.join('?' for _ in vals)+')');params.extend(vals)
        return (' AND '+' AND '.join(clauses)) if clauses else '',params
    def lexical(self,query:str,limit:int=60,filters:dict[str,Any]|None=None)->tuple[list[dict[str,Any]],str|None]:
        toks=[t.lower() for t in TOKEN_RE.findall(query) if len(t)>=2 and t.lower() not in STOPWORDS][:12]
        if not toks:return [],None
        variants=[]
        if len(toks)>1:
            variants.append(('phrase','"'+' '.join(toks).replace('"','')+'"')); variants.append(('and',' AND '.join(f'"{t}"' for t in toks)))
        variants.append(('or',' OR '.join(f'"{t}"' for t in toks)))
        filter_sql,filter_params=self._filter_sql(filters,'d')
        out=[];seen=set();modes=[]
        sql='SELECT chunks_fts.rowid,bm25(chunks_fts,2.0,1.0) AS bm FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid JOIN documents d ON d.id=c.document_id WHERE chunks_fts MATCH ?'+filter_sql+' ORDER BY bm LIMIT ?'
        for mode,expr in variants:
            try:self.q.execute(sql,(expr,*filter_params,limit)); rows=self.q.fetchall()
            except Exception:continue
            if rows:modes.append(mode)
            for rowid,bm in rows:
                cid=int(rowid)
                if cid in seen:continue
                seen.add(cid); h=self._fetch_chunk(cid);h['bm25']=float(bm);h['lexical_rank']=len(out)+1;h['lexical_variant']=mode;out.append(h)
                if len(out)>=limit:break
            if len(out)>=limit:break
        return out,'+'.join(modes) if modes else None
    def vector(self,query_vector:list[float],limit:int=80,filters:dict[str,Any]|None=None)->tuple[list[dict[str,Any]],str]:
        vec_json=json.dumps(query_vector);out=[]
        if filters:
            filter_sql,filter_params=self._filter_sql(filters,'d')
            sql='SELECT e.chunk_id,vector_distance_cos(e.embedding,vector(?)) AS dist FROM chunk_embeddings e JOIN chunks c ON c.id=e.chunk_id JOIN documents d ON d.id=c.document_id WHERE 1=1'+filter_sql+' ORDER BY dist ASC LIMIT ?'
            self.q.execute(sql,(vec_json,*filter_params,limit)); pairs=[(int(cid),float(dist)) for cid,dist in self.q.fetchall()];mode='filtered_exact_cosine'
        else:
            self.q.execute("SELECT id FROM vector_top_k('idx_chunk_embeddings_vector',vector(?),?)",(vec_json,limit)); ids=[int(r[0]) for r in self.q.fetchall()];pairs=[];mode='vector_top_k'
            for cid in ids:
                self.q.execute('SELECT vector_distance_cos(embedding,vector(?)) FROM chunk_embeddings WHERE chunk_id=?',(vec_json,cid)); row=self.q.fetchone()
                if row:pairs.append((cid,float(row[0])))
        for rank,(cid,dist) in enumerate(pairs,1):
            h=self._fetch_chunk(cid);h['distance']=dist;h['vector_similarity']=1.0-dist;h['vector_rank']=rank;out.append(h)
        return out,mode
    def vector_similarity_for_range(self,query_vector:list[float],source_id:str,segment_start_id:int,segment_end_id:int)->float|None:
        vec_json=json.dumps(query_vector)
        self.q.execute('SELECT MAX(1.0-vector_distance_cos(e.embedding,vector(?))) FROM chunk_embeddings e JOIN chunks c ON c.id=e.chunk_id WHERE c.document_id=? AND c.segment_end_id>=? AND c.segment_start_id<=?',(vec_json,source_id,int(segment_start_id),int(segment_end_id)))
        row=self.q.fetchone(); return None if not row or row[0] is None else float(row[0])
