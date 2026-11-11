"""Single-GPU, authenticated model service with bounded queues and separate embedding/query semantics."""

import argparse
import hashlib
import hmac
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

TASK = "Given a product declaration, retrieve applicable HS tariff headings and analogous customs classification cases."


class EmbeddingInput(BaseModel):
    model: str = "Qwen3-Embedding-8B"
    input: list[str] = Field(min_length=1, max_length=16)
    input_type: Literal["query", "document"] = "document"


class RerankInput(BaseModel):
    query: str = Field(min_length=1, max_length=12000)
    documents: list[str] = Field(min_length=1, max_length=100)


class ChatInput(BaseModel):
    model: str = "Qwen3-4B"
    messages: list[dict] = Field(min_length=1, max_length=20)
    max_tokens: int = Field(default=1536, ge=1, le=2048)
    temperature: float = Field(default=0.2, ge=0, le=2)


class Backend:
    def __init__(self, root):
        import torch
        from sentence_transformers import SentenceTransformer
        from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

        if not torch.cuda.is_available():
            raise RuntimeError("3090 CUDA unavailable")
        self.torch = torch
        self.encoder = SentenceTransformer(
            str(root / "Qwen3-Embedding-8B"),
            device="cuda",
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs={"torch_dtype": torch.float16, "attn_implementation": "sdpa"},
        )
        self.encoder.max_seq_length = 1024
        self.rerank_tokenizer = AutoTokenizer.from_pretrained(
            root / "Qwen3-Reranker-0.6B", padding_side="left", local_files_only=True
        )
        self.reranker = (
            AutoModelForCausalLM.from_pretrained(
                root / "Qwen3-Reranker-0.6B",
                torch_dtype=torch.float16,
                attn_implementation="sdpa",
                local_files_only=True,
            )
            .cuda()
            .eval()
        )
        self.yes = self.rerank_tokenizer.convert_tokens_to_ids("yes")
        self.no = self.rerank_tokenizer.convert_tokens_to_ids("no")
        self.tokenizer = AutoTokenizer.from_pretrained(root / "Qwen3-4B", local_files_only=True)
        self.generator = AutoModelForCausalLM.from_pretrained(
            root / "Qwen3-4B",
            local_files_only=True,
            device_map={"": 0},
            torch_dtype=torch.float16,
            attn_implementation="sdpa",
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16,
                bnb_4bit_use_double_quant=True,
            ),
        ).eval()

    def embed(self, texts, query=False):
        return self.encoder.encode(
            texts,
            batch_size=1,
            normalize_embeddings=True,
            prompt="Instruct: " + TASK + "\nQuery: " if query else "",
        )

    def rerank(self, query, documents):
        tokenizer = self.rerank_tokenizer
        prefix = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
        suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        head = tokenizer.encode(prefix, add_special_tokens=False)
        tail = tokenizer.encode(suffix, add_special_tokens=False)
        scores = []
        with self.torch.inference_mode():
            for offset in range(0, len(documents), 8):
                encoded = []
                for document in documents[offset : offset + 8]:
                    body = tokenizer.encode(
                        "<Instruct>: " + TASK + "\n<Query>: " + query + "\n<Document>: " + document,
                        add_special_tokens=False,
                        truncation=True,
                        max_length=1536 - len(head) - len(tail),
                    )
                    encoded.append(head + body + tail)
                inputs = tokenizer.pad({"input_ids": encoded}, padding=True, return_tensors="pt").to("cuda")
                logits = self.reranker(**inputs, logits_to_keep=1, use_cache=False).logits[:, -1, :]
                probabilities = self.torch.softmax(logits[:, [self.no, self.yes]].float(), dim=1)[:, 1]
                scores.extend(probabilities.cpu().tolist())
        return scores

    def generate(self, messages, max_tokens, temperature):
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True, enable_thinking=False
        )
        inputs = self.tokenizer(text, return_tensors="pt").to("cuda")
        length = inputs.input_ids.shape[1]
        if length + max_tokens > 8192:
            raise ValueError("Context exceeds the configured 8192-token budget")
        options = {
            "max_new_tokens": max_tokens,
            "do_sample": temperature > 0,
            "pad_token_id": self.tokenizer.eos_token_id,
        }
        if temperature > 0:
            options.update(temperature=temperature, top_p=0.8)
        with self.torch.inference_mode():
            output = self.generator.generate(**inputs, **options)
        result = output[0, length:]
        return self.tokenizer.decode(result, skip_special_tokens=True), {
            "prompt_tokens": length,
            "completion_tokens": len(result),
            "total_tokens": length + len(result),
        }


def create_app(root, key, backend=None):
    if len(key) < 24:
        raise ValueError("Configure a private model gateway key")
    lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        app.state.backend = backend or Backend(root)
        yield

    app = FastAPI(title="Tariff GPU inference gateway", lifespan=lifespan, docs_url=None)

    def auth(authorization: str = Header(default="")):
        if not hmac.compare_digest(authorization, "Bearer " + key):
            raise HTTPException(401, "Unauthorized")

    def acquire():
        if not lock.acquire(timeout=120):
            raise HTTPException(503, "GPU queue is busy")

    @app.get("/health", dependencies=[Depends(auth)])
    def health():
        return {
            "service_revision": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:16],
            "rerank_batch_size": 8,
            "status": "ready",
            "embedding_model": "Qwen3-Embedding-8B",
            "reranker": "Qwen3-Reranker-0.6B",
            "small_model": "Qwen3-4B",
            "small_quantization": "nf4",
            "embedding_quantization": "fp16",
            "device": "cuda",
        }

    @app.get("/v1/models", dependencies=[Depends(auth)])
    def models():
        return {"data": [{"id": name} for name in ("Qwen3-Embedding-8B", "Qwen3-4B")]}

    @app.post("/v1/embeddings", dependencies=[Depends(auth)])
    def embed(body: EmbeddingInput):
        if body.model != "Qwen3-Embedding-8B":
            raise HTTPException(400, "Unknown embedding model")
        if any(len(x) > 12000 for x in body.input):
            raise HTTPException(413, "Input exceeds encoding budget")
        acquire()
        try:
            vectors = app.state.backend.embed(body.input, body.input_type == "query")
        finally:
            lock.release()
        return {"data": [{"index": i, "embedding": row.tolist()} for i, row in enumerate(vectors)]}

    @app.post("/rerank", dependencies=[Depends(auth)])
    def rerank(body: RerankInput):
        acquire()
        try:
            scores = app.state.backend.rerank(body.query, body.documents)
        finally:
            lock.release()
        return {
            "results": [
                {"index": i, "score": scores[i]} for i in sorted(range(len(scores)), key=lambda i: -scores[i])
            ]
        }

    @app.post("/v1/chat/completions", dependencies=[Depends(auth)])
    def generate(body: ChatInput):
        if body.model != "Qwen3-4B":
            raise HTTPException(400, "Unknown generation model")
        acquire()
        try:
            try:
                text, usage = app.state.backend.generate(body.messages, body.max_tokens, body.temperature)
            except ValueError as exc:
                raise HTTPException(413, str(exc)) from None
        finally:
            lock.release()
        return {
            "model": body.model,
            "choices": [{"message": {"role": "assistant", "content": text}}],
            "usage": usage,
        }

    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18081)
    args = parser.parse_args()
    import uvicorn

    private = args.root / "data/private"
    uvicorn.run(
        create_app(args.root / "models", (private / "gateway.key").read_text().strip()),
        host=args.host,
        port=args.port,
        ssl_certfile=str(private / "server.crt"),
        ssl_keyfile=str(private / "server.key"),
    )


if __name__ == "__main__":
    main()
