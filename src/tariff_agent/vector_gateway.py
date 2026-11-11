"""Loopback-only Qwen3 embedding / reranker gateway. No generation model is loaded."""

import argparse
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field


class Embeddings(BaseModel):
    model: str = "Qwen3-Embedding-8B"
    input: list[str] = Field(min_length=1, max_length=16)


class Rerank(BaseModel):
    query: str = Field(min_length=1, max_length=12000)
    documents: list[str] = Field(min_length=1, max_length=100)


def create_app(embedding_path, reranker_path, device="cuda", rerank_device="cpu"):
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        import torch
        from sentence_transformers import CrossEncoder, SentenceTransformer

        if ("cuda" in device or "cuda" in rerank_device) and not torch.cuda.is_available():
            raise RuntimeError("CUDA unavailable; restore GPU or explicitly select --device cpu")

        def kwargs(target):
            return {
                "torch_dtype": torch.float16 if "cuda" in target else torch.float32,
                "attn_implementation": "eager",
            }

        app.state.encoder = SentenceTransformer(
            str(embedding_path),
            device=device,
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs=kwargs(device),
        )
        app.state.encoder.max_seq_length = 1024
        app.state.reranker = CrossEncoder(
            str(reranker_path),
            device=rerank_device,
            local_files_only=True,
            trust_remote_code=False,
            max_length=2048,
            model_kwargs=kwargs(rerank_device),
        )
        yield

    app = FastAPI(title="Private Qwen3 vector gateway", lifespan=lifespan, docs_url=None)

    @app.get("/health")
    def health():
        return {
            "status": "ready",
            "embedding_model": "Qwen3-Embedding-8B",
            "reranker": "Qwen3-Reranker-0.6B",
            "device": device,
            "rerank_device": rerank_device,
        }

    @app.get("/v1/models")
    def models():
        return {"data": [{"id": "Qwen3-Embedding-8B"}]}

    @app.post("/v1/embeddings")
    def embed(req: Embeddings):
        if req.model != "Qwen3-Embedding-8B":
            raise HTTPException(400, "Unknown embedding model")
        if any(len(x) > 12000 for x in req.input):
            raise HTTPException(413, "Input exceeds configured encoding budget")
        with lock:
            vectors = app.state.encoder.encode(req.input, batch_size=1, normalize_embeddings=True)
        return {"data": [{"index": i, "embedding": v.tolist()} for i, v in enumerate(vectors)]}

    @app.post("/rerank")
    def rerank(req: Rerank):
        if any(len(x) > 16000 for x in req.documents):
            raise HTTPException(413, "Document exceeds configured reranking budget")
        with lock:
            scores = app.state.reranker.predict([(req.query, x) for x in req.documents], batch_size=1)
        values = [float(x) for x in scores]
        return {
            "results": [
                {"index": i, "score": values[i]}
                for i in sorted(range(len(values)), key=lambda i: (-values[i], i))
            ]
        }

    return app


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--embedding", default="models/Qwen3-Embedding-8B")
    p.add_argument("--reranker", default="models/Qwen3-Reranker-0.6B")
    p.add_argument("--device", choices=["cuda", "cpu"], default="cuda")
    p.add_argument("--rerank-device", choices=["cuda", "cpu"], default="cpu")
    p.add_argument("--port", type=int, default=8011)
    a = p.parse_args()
    import uvicorn

    uvicorn.run(create_app(a.embedding, a.reranker, a.device, a.rerank_device), host="127.0.0.1", port=a.port)


if __name__ == "__main__":
    main()
