"""Vector store del caso normativa.

- Con PINECONE_API_KEY: Pinecone serverless con embeddings integrados (llama-text-embed-v2,
  1024 dims, coseno) y reranking multilingüe (bge-reranker-v2-m3) vía Pinecone Inference.
- Sin clave (o si Pinecone falla al iniciar): índice local en data/local_index_<ns>.json con
  similitud léxica. Sirve para desarrollar sin red y como plan B de la demo.

Uso:
    from app.normas.vector import get_store
    store = get_store()
    store.upsert([{"id": "SINT-02#art-5#c0", "text": "Artículo 5...", "doc_id": "SINT-02", ...}])
    hits = store.search("límite de endeudamiento", top_k=20, filter={"estado": "vigente"}, rerank_top_n=5)
    hits[0].id, hits[0].score, hits[0].rerank_score, hits[0].text, hits[0].metadata

Metadatos por registro (ver docs/CONTRATOS.md): doc_id, nombre_norma, entidad, tipo, tema, articulo,
pagina, fecha_emision (int AAAAMMDD), fecha_vigencia (int AAAAMMDD), estado, version, reemplaza_a,
codigo, tema, temas (lista).
Filtros con la sintaxis de Pinecone: {"campo": valor}, $eq $ne $in $nin $gt $gte $lt $lte, $and, $or.
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings

log = logging.getLogger("vector")

ROOT = Path(__file__).resolve().parents[2]
TEXT_FIELD = "chunk_text"


@dataclass
class Hit:
    id: str
    score: float                       # similitud densa (o léxica en el respaldo local)
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    rerank_score: float | None = None  # score del reranker (None si no se aplicó)

    @property
    def best_score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.score

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "score": round(self.score, 4),
                "rerank_score": None if self.rerank_score is None else round(self.rerank_score, 4),
                "text": self.text, "metadata": self.metadata}


class VectorStore:
    backend = "base"

    def upsert(self, records: list[dict[str, Any]]) -> int:
        raise NotImplementedError

    def search(self, text: str, top_k: int = 10, filter: dict[str, Any] | None = None,
               rerank_top_n: int | None = None) -> list[Hit]:
        raise NotImplementedError

    def delete_ids(self, ids: list[str]) -> None:
        raise NotImplementedError

    def delete_all(self) -> None:
        raise NotImplementedError

    def contar_prefijo(self, prefijo: str) -> int:
        """Cantidad de registros cuyo id empieza con `prefijo` (para verificar idempotencia)."""
        raise NotImplementedError


# ---------------------------------------------------------------------------- Pinecone
class PineconeStore(VectorStore):
    backend = "pinecone"

    def __init__(self) -> None:
        from pinecone import Pinecone  # import diferido: el respaldo local no lo necesita

        s = get_settings()
        self.s = s
        self.pc = Pinecone(api_key=s.pinecone_api_key)
        if not self.pc.has_index(s.pinecone_index):
            log.info("Creando índice Pinecone %s (%s)", s.pinecone_index, s.pinecone_embed_model)
            self.pc.create_index_for_model(
                name=s.pinecone_index, cloud=s.pinecone_cloud, region=s.pinecone_region,
                embed={"model": s.pinecone_embed_model, "field_map": {"text": TEXT_FIELD}},
            )
        self.index = self.pc.Index(s.pinecone_index)
        self.ns = s.pinecone_namespace

    def upsert(self, records: list[dict[str, Any]]) -> int:
        rows = [_to_pinecone(r) for r in records]
        for i in range(0, len(rows), 90):  # límite de 96 registros por llamada con embeddings integrados
            self.index.upsert_records(self.ns, rows[i:i + 90])
        return len(rows)

    def search(self, text: str, top_k: int = 10, filter: dict[str, Any] | None = None,
               rerank_top_n: int | None = None) -> list[Hit]:
        query: dict[str, Any] = {"inputs": {"text": text}, "top_k": top_k}
        if filter:
            query["filter"] = filter
        res = self.index.search(namespace=self.ns, query=query)
        hits = []
        for h in res["result"]["hits"]:
            fields = dict(h.get("fields") or {})
            body = fields.pop(TEXT_FIELD, "")
            hits.append(Hit(id=h["_id"], score=float(h["_score"]), text=body, metadata=fields))
        if rerank_top_n and hits and self.s.use_rerank:
            hits = self._rerank(text, hits, rerank_top_n)
        return hits

    def _rerank(self, text: str, hits: list[Hit], top_n: int) -> list[Hit]:
        try:
            rr = self.pc.inference.rerank(
                model=self.s.pinecone_rerank_model, query=text,
                documents=[{"id": h.id, "text": h.text} for h in hits],
                top_n=min(top_n, len(hits)), return_documents=False,
            )
        except Exception:
            log.exception("Rerank falló; se usan los scores densos")
            return hits[:top_n]
        out = []
        for row in rr.data:
            h = hits[row["index"]]
            h.rerank_score = float(row["score"])
            out.append(h)
        return out

    def delete_ids(self, ids: list[str]) -> None:
        if ids:
            self.index.delete(ids=ids, namespace=self.ns)

    def contar_prefijo(self, prefijo: str) -> int:
        n = 0
        for ids in self.index.list(prefix=prefijo, namespace=self.ns):
            n += len(ids)
        return n

    def delete_all(self) -> None:
        try:
            self.index.delete(delete_all=True, namespace=self.ns)
        except Exception as e:  # el namespace puede no existir todavía
            log.warning("delete_all en %s: %s", self.ns, e)


def _to_pinecone(r: dict[str, Any]) -> dict[str, Any]:
    row = {k: v for k, v in r.items() if k not in ("id", "text") and v is not None}
    row["_id"] = r["id"]
    row[TEXT_FIELD] = r["text"]
    return row


# ---------------------------------------------------------------------------- Respaldo local
_STOP = set("de la el los las y o a en por para con del al que se un una es su sus lo como no sin "
            "sobre entre cual este esta estos estas ser son debe deben".split())


def _tokens(text: str) -> list[str]:
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return [w for w in re.findall(r"[a-z0-9]+", t) if w not in _STOP and len(w) > 1]


_OPS = {
    "$eq": lambda v, r: v == r, "$ne": lambda v, r: v != r,
    # listas en metadatos (p. ej. temas): $in/$nin por intersección, como Pinecone
    "$in": lambda v, r: bool(set(v) & set(r)) if isinstance(v, list) else v in r,
    "$nin": lambda v, r: not (set(v) & set(r)) if isinstance(v, list) else v not in r,
    "$gt": lambda v, r: v is not None and v > r, "$gte": lambda v, r: v is not None and v >= r,
    "$lt": lambda v, r: v is not None and v < r, "$lte": lambda v, r: v is not None and v <= r,
}


def _match(meta: dict[str, Any], flt: dict[str, Any] | None) -> bool:
    if not flt:
        return True
    for key, cond in flt.items():
        if key == "$and":
            if not all(_match(meta, c) for c in cond):
                return False
        elif key == "$or":
            if not any(_match(meta, c) for c in cond):
                return False
        else:
            ops = cond if isinstance(cond, dict) else {"$eq": cond}
            if not all(_OPS[op](meta.get(key), ref) for op, ref in ops.items()):
                return False
    return True


class LocalStore(VectorStore):
    backend = "local"

    def __init__(self) -> None:
        ns = get_settings().pinecone_namespace
        self.path = ROOT / "data" / f"local_index_{ns}.json"
        self.lock = threading.Lock()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def upsert(self, records: list[dict[str, Any]]) -> int:
        with self.lock:
            data = self._load()
            for r in records:
                data[r["id"]] = dict(r)
            self._save(data)
        return len(records)

    def search(self, text: str, top_k: int = 10, filter: dict[str, Any] | None = None,
               rerank_top_n: int | None = None) -> list[Hit]:
        q = Counter(_tokens(text))
        qn = math.sqrt(sum(v * v for v in q.values())) or 1.0
        scored = []
        for r in self._load().values():
            meta = {k: v for k, v in r.items() if k not in ("id", "text")}
            if not _match(meta, filter):
                continue
            d = Counter(_tokens(r["text"]))
            dot = sum(q[w] * d[w] for w in q)
            if not dot:
                continue
            dn = math.sqrt(sum(v * v for v in d.values())) or 1.0
            scored.append(Hit(id=r["id"], score=dot / (qn * dn), text=r["text"], metadata=meta))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[: rerank_top_n or top_k]

    def delete_ids(self, ids: list[str]) -> None:
        with self.lock:
            data = self._load()
            for i in ids:
                data.pop(i, None)
            self._save(data)

    def delete_all(self) -> None:
        with self.lock:
            self._save({})

    def contar_prefijo(self, prefijo: str) -> int:
        return sum(1 for k in self._load() if k.startswith(prefijo))


@lru_cache
def get_store() -> VectorStore:
    s = get_settings()
    if s.pinecone_api_key:
        for attempt in range(3):
            try:
                store = PineconeStore()
                log.info("Vector store: Pinecone índice=%s ns=%s", s.pinecone_index, s.pinecone_namespace)
                return store
            except Exception as e:
                log.warning("Pinecone no disponible (intento %d): %s", attempt + 1, e)
                time.sleep(1 + attempt)
        log.error("Pinecone no respondió: se usa el índice local de respaldo")
    else:
        log.info("Vector store: índice local (sin PINECONE_API_KEY)")
    return LocalStore()


def fecha_int(fecha: str | None) -> int | None:
    """'2025-03-01' -> 20250301 (para filtros numéricos en Pinecone)."""
    if not fecha:
        return None
    digits = re.sub(r"\D", "", fecha)[:8]
    return int(digits) if len(digits) == 8 else None
