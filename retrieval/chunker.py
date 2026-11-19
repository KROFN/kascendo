from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass
from typing import Any
from .adapter import CanonicalDocument
from .config import CHUNKER_VERSION, TOKEN_RE

def fmt_ts(seconds: float) -> str:
    ms=max(0,int(round(float(seconds)*1000))); s,milli=divmod(ms,1000); h,rem=divmod(s,3600); m,sec=divmod(rem,60)
    return f"{h:02d}:{m:02d}:{sec:02d}.{milli:03d}"

def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def stable_int_id(*parts: Any) -> int:
    raw="\x1f".join(str(x) for x in parts).encode("utf-8")
    return int(hashlib.sha256(raw).hexdigest()[:15],16)

def estimate_tokens(text: str) -> int:
    words=TOKEN_RE.findall(text); punctuation=re.findall(r"[^\w\s]",text,re.UNICODE)
    return len(words)+max(0,len(punctuation)//3)

@dataclass(frozen=True)
class Chunk:
    id: int
    document_id: str
    ordinal: int
    segment_start_id: int
    segment_end_id: int
    time_start: float
    time_end: float
    text: str
    embedding_text: str
    token_count: int
    text_hash: str

class TimestampChunker:
    def __init__(self,target_tokens:int=420,hard_max_tokens:int=620,overlap_segments:int=1):
        self.target_tokens=target_tokens; self.hard_max_tokens=hard_max_tokens; self.overlap_segments=overlap_segments
    def chunk(self,doc:CanonicalDocument)->list[Chunk]:
        out=[]; segs=list(doc.segments); i=0; ordinal=0
        while i<len(segs):
            j=i; parts=[]; tok=0
            while j<len(segs):
                ct=estimate_tokens(segs[j].text)
                if j>i and tok>=self.target_tokens: break
                if j>i and tok+ct>self.hard_max_tokens: break
                parts.append(segs[j].text); tok+=ct; j+=1
            if j==i:
                parts=[segs[i].text]; tok=estimate_tokens(segs[i].text); j=i+1
            first,last=segs[i],segs[j-1]; body=" ".join(x for x in parts if x).strip()
            prefix=(f"Title: {doc.title}\nSubject: {doc.subject}\nCourse year: {doc.course_year}\n"
                    f"Timestamp: {fmt_ts(first.start)}-{fmt_ts(last.end)}\nTranscript:\n")
            cid=stable_int_id(doc.source_id,CHUNKER_VERSION,first.id,last.id,text_hash(body))
            out.append(Chunk(cid,doc.source_id,ordinal,first.id,last.id,first.start,last.end,body,prefix+body,tok,text_hash(body)))
            ordinal+=1
            if j>=len(segs): break
            i=max(i+1,j-self.overlap_segments)
        return out
