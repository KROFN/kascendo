from __future__ import annotations
import argparse
import json
from pathlib import Path
from .adapter import ASRAdapter
from .chunker import TimestampChunker
from .config import DEFAULT_ENDPOINT
from .course import CourseManifest
from .db import RetrievalDB
from .embed import EmbedClient
from .engine import SearchEngine

def build_index(db_path:Path,transcript:Path,endpoint:str):
    doc=ASRAdapter.load(transcript);chunks=TimestampChunker().chunk(doc);db=RetrievalDB(db_path)
    try:
        db.init_schema();result=db.index_document(doc,chunks,EmbedClient(endpoint));result.update({'source_id':doc.source_id,'title':doc.title,'duration_seconds':doc.duration_seconds,'segments':len(doc.segments),'db':str(db_path)});return result
    finally:db.close()

def main():
    ap=argparse.ArgumentParser(description='K.ascendo retrieval index and search CLI');ap.add_argument('--db',default='retrieval.db');ap.add_argument('--endpoint',default=DEFAULT_ENDPOINT);sub=ap.add_subparsers(dest='cmd',required=True)
    ix=sub.add_parser('index');ix.add_argument('transcript')
    se=sub.add_parser('search');se.add_argument('query');se.add_argument('--max-clips',type=int,default=3);se.add_argument('--roundtrip',action='store_true');se.add_argument('--debug',action='store_true')
    se.add_argument('--provider');se.add_argument('--subject');se.add_argument('--course-year');se.add_argument('--course-id');se.add_argument('--source-id');se.add_argument('--sequence',type=int)
    sub.add_parser('courses')
    ic=sub.add_parser('index-course');ic.add_argument('manifest')
    ci=sub.add_parser('inspect-course');ci.add_argument('manifest')
    sub.add_parser('status');args=ap.parse_args();dbp=Path(args.db)
    if args.cmd=='index':print(json.dumps(build_index(dbp,Path(args.transcript),args.endpoint),ensure_ascii=False,indent=2));return
    if args.cmd=='inspect-course':
        manifest=CourseManifest.load(args.manifest);print(json.dumps(manifest.inspect(strict=False),ensure_ascii=False,indent=2));return
    if args.cmd=='courses':
        db=RetrievalDB(dbp)
        try:db.init_schema();print(json.dumps({'courses':db.list_courses()},ensure_ascii=False,indent=2))
        finally:db.close()
        return
    if args.cmd=='index-course':
        manifest=CourseManifest.load(args.manifest);inspection=manifest.inspect(strict=True);db=RetrievalDB(dbp);db.init_schema();rows=[]
        try:
            db.register_course(manifest.course_id,manifest.content_kind,manifest.source_root,manifest.db_config(inspection));chunker=TimestampChunker();embedder=EmbedClient(args.endpoint);excluded=set(manifest.index_exclude_sequences)
            for i,p in enumerate(manifest.discover(),1):
                doc=ASRAdapter.load(p,corpus=manifest.course_id)
                if doc.sequence in excluded:
                    row={'i':i,'sequence':doc.sequence,'source_id':doc.source_id,'title':doc.title,'skipped_index_policy':True};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True);continue
                chunks=chunker.chunk(doc);result=db.index_document(doc,chunks,embedder);row={'i':i,'sequence':doc.sequence,'source_id':doc.source_id,'title':doc.title,**result};rows.append(row);print(json.dumps(row,ensure_ascii=False),flush=True)
            print(json.dumps({'course':inspection,'index':db.status(),'documents':rows},ensure_ascii=False,indent=2));return
        finally:db.close()
    if args.cmd=='status':
        db=RetrievalDB(dbp)
        try:
            db.q.execute('SELECT COUNT(*) FROM documents');docs=db.q.fetchone()[0];db.q.execute('SELECT COUNT(*) FROM chunks');chunks=db.q.fetchone()[0];db.q.execute('SELECT COUNT(*) FROM chunk_embeddings');vecs=db.q.fetchone()[0]
            print(json.dumps({'documents':docs,'chunks':chunks,'vectors':vecs,'generation':db.meta('generation',0),'embedding_identity':db.meta('embedding_identity',{}),'courses':db.list_courses()},ensure_ascii=False,indent=2))
        finally:db.close()
        return
    eng=SearchEngine(dbp,EmbedClient(args.endpoint))
    try:
        filters={k:v for k,v in {'provider':args.provider,'subject':args.subject,'course_year':args.course_year,'course_id':args.course_id,'source_id':args.source_id,'sequence':args.sequence}.items() if v is not None}
        out=eng.search(args.query,args.max_clips,filters=filters,debug=args.debug)
        if args.roundtrip:out['roundtrip']=[eng.roundtrip_clip(c) for c in out['clips']]
        print(json.dumps(out,ensure_ascii=False,indent=2))
    finally:eng.close()
if __name__=='__main__':main()
