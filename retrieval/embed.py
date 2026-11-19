from __future__ import annotations
import json
import socket
import time
import urllib.error
import urllib.request
from .config import DEFAULT_ENDPOINT, EMBEDDING_DIM, QUERY_INSTRUCTION

class EmbedClient:
    def __init__(self,endpoint:str=DEFAULT_ENDPOINT,timeout:int=180,retries:int=3):
        self.endpoint=endpoint; self.timeout=timeout; self.retries=retries
    def _post(self,inputs:list[str])->list[list[float]]:
        body=json.dumps({"input":inputs},ensure_ascii=False).encode("utf-8")
        req=urllib.request.Request(self.endpoint,data=body,headers={"Content-Type":"application/json"},method="POST")
        last=None
        for attempt in range(1,self.retries+1):
            try:
                with urllib.request.urlopen(req,timeout=self.timeout) as r:
                    payload=json.loads(r.read().decode("utf-8"))
                rows=sorted(payload["data"],key=lambda x:int(x["index"]))
                embs=[list(map(float,x["embedding"])) for x in rows]
                if len(embs)!=len(inputs):raise RuntimeError(f"embedding row count mismatch: {len(embs)} != {len(inputs)}")
                for emb in embs:
                    if len(emb)!=EMBEDDING_DIM: raise RuntimeError(f"embedding dimension mismatch: {len(emb)} != {EMBEDDING_DIM}")
                return embs
            except (TimeoutError,socket.timeout,urllib.error.URLError,urllib.error.HTTPError,ConnectionError) as e:
                last=e
                if attempt>=self.retries:break
                time.sleep(min(8,2**attempt))
        raise RuntimeError(f"embedding request failed after {self.retries} attempts: {last}") from last
    def embed_documents(self,texts:list[str],batch:int=16)->list[list[float]]:
        out=[]
        for i in range(0,len(texts),batch): out.extend(self._post(texts[i:i+batch]))
        return out
    def embed_query(self,query:str)->list[float]:
        formatted=f"Instruct: {QUERY_INSTRUCTION}\nQuery: {query.strip()}"
        return self._post([formatted])[0]
