"""Local MiniLM embeddings and validated, content-addressed vector caches."""

import json
import math
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

from src.common.json_io import digest, write_json
from src.common.models import MemoryBundle

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"


class Encoder(Protocol):
    @property
    def metadata(self) -> dict: ...

    def encode(self, texts: list[str]) -> list[list[float]]: ...


class MiniLMEncoder:
    """Lazy loading keeps the base BM25 installation independent of torch."""

    def __init__(
        self,
        *,
        device: str = "cpu",
        cache_folder: Path = Path("cache/embedding_models"),
        local_files_only: bool = False,
    ) -> None:
        self.device = device
        self.cache_folder = cache_folder
        self.local_files_only = local_files_only
        self._model = None

    @property
    def metadata(self) -> dict:
        try:
            libraries = {
                name: version(name)
                for name in ("sentence-transformers", "transformers", "torch", "tokenizers")
            }
        except ImportError:
            raise RuntimeError(
                "Install vector dependencies: pip install -r requirements-vector.txt"
            ) from None
        return {
            "model": MODEL_NAME,
            "revision": MODEL_REVISION,
            "dimension": 384,
            "device": self.device,
            "libraries": libraries,
            "encoding": "normalized-content-only-v1",
            "max_tokens": 256,
        }

    def encode(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError:
                raise RuntimeError(
                    "Install vector dependencies: pip install -r requirements-vector.txt"
                ) from None
            self._model = SentenceTransformer(
                MODEL_NAME,
                revision=MODEL_REVISION,
                device=self.device,
                cache_folder=str(self.cache_folder),
                local_files_only=self.local_files_only,
                trust_remote_code=False,
            )
        tokens = self._model.tokenizer(
            texts, truncation=False, padding=False, add_special_tokens=True, verbose=False
        )["input_ids"]
        for i, token_ids in enumerate(tokens):
            if len(token_ids) > self._model.max_seq_length:
                raise ValueError(
                    f"Embedding text {i} exceeds {self._model.max_seq_length} tokens; "
                    "split it into shorter memories/questions instead of silently truncating"
                )
        return self._model.encode(
            texts,
            batch_size=32,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).tolist()


def validate_vectors(value: object, count: int, dimension: int) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != count:
        raise ValueError("Vector row count mismatch")
    rows = []
    for row in value:
        if not isinstance(row, list) or len(row) != dimension:
            raise ValueError("Vector dimension mismatch")
        if any(type(x) not in (int, float) or not math.isfinite(x) for x in row):
            raise ValueError("Vectors must contain finite numbers")
        norm = math.sqrt(math.fsum(x * x for x in row))
        if not math.isfinite(norm) or norm <= 0:
            raise ValueError("Vectors must have a finite nonzero norm")
        rows.append([float(x / norm) for x in row])
    return rows


@dataclass
class VectorStore:
    encoder: Encoder
    metadata: dict
    rows: list[list[float]]
    cache_path: Path

    def scores(self, query: str, positions: list[int]) -> list[tuple[float, int]]:
        if not positions:
            return []
        if self.encoder.metadata != self.metadata["encoder"]:
            raise RuntimeError("Encoder changed since index construction; rebuild the index")
        vector = validate_vectors(
            self.encoder.encode([query]), 1, self.metadata["encoder"]["dimension"]
        )[0]
        return [
            (
                max(
                    -1.0,
                    min(1.0, math.fsum(a * b for a, b in zip(vector, self.rows[i], strict=True))),
                ),
                i,
            )
            for i in positions
        ]


def build_vectors(bundle: MemoryBundle, encoder: Encoder, cache_dir: Path) -> VectorStore:
    metadata = {
        "format": "memory-vectors-v1",
        "conversation_id": bundle.conversation_id,
        "build_id": bundle.build_info.build_id,
        "bundle_sha256": digest(bundle.model_dump(mode="json")),
        "memory_ids": [m.memory_id for m in bundle.memories],
        "encoder": encoder.metadata,
    }
    dimension = metadata["encoder"]["dimension"]
    if type(dimension) is not int or dimension < 1:
        raise ValueError("Encoder dimension must be a positive integer")
    path = cache_dir / f"{digest(metadata)}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(cached, dict)
            or cached.get("metadata") != metadata
            or cached.get("vectors_sha256") != digest(cached.get("vectors"))
        ):
            raise ValueError(f"Invalid vector cache; remove and rebuild: {path}")
        rows = validate_vectors(cached["vectors"], len(bundle.memories), dimension)
    else:
        rows = validate_vectors(
            encoder.encode([memory.content for memory in bundle.memories]),
            len(bundle.memories),
            dimension,
        )
        write_json({"metadata": metadata, "vectors": rows, "vectors_sha256": digest(rows)}, path)
    return VectorStore(encoder=encoder, metadata=metadata, rows=rows, cache_path=path)
