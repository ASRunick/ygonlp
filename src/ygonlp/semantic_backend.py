"""The single production embedding backend for semantic effect-text retrieval."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib.metadata import version
from typing import Protocol, Sequence

import numpy as np


MODEL_ID = "minishlab/potion-base-8M"
MODEL_REVISION = "bf8b056651a2c21b8d2565580b8569da283cab23"


@dataclass(frozen=True)
class EmbeddingSpec:
    provider: str
    model_id: str
    package_version: str
    model_revision: str | None
    dimension: int
    pooling: str
    normalization: str
    max_length: int
    dtype: str

    def metadata(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_SPEC = EmbeddingSpec(
    provider="model2vec", model_id=MODEL_ID, package_version="0.9.0",
    model_revision=MODEL_REVISION, dimension=256, pooling="token_mean",
    normalization="l2", max_length=512, dtype="float32",
)


class Embedder(Protocol):
    def encode(self, texts: Sequence[str]) -> np.ndarray: ...


class Model2VecEmbedder:
    """Load only the pinned snapshot; construction can download it."""

    def __init__(self) -> None:
        try:
            import model2vec
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise RuntimeError('意味検索には python -m pip install ".[semantic]" が必要です') from exc
        if version("model2vec") != DEFAULT_SPEC.package_version:
            raise RuntimeError("model2vec のバージョンが指定と一致しません")
        folder = snapshot_download(
            repo_id=MODEL_ID, revision=MODEL_REVISION,
            allow_patterns=["config.json", "model.safetensors", "tokenizer.json", "README.md"],
        )
        self._model = model2vec.StaticModel.from_pretrained(folder, normalize=True)
        if self._model.dim != DEFAULT_SPEC.dimension or self._model.normalize is not True:
            raise RuntimeError("固定した embedding model の dimension または normalization が不正です")

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self._model.encode(list(texts), max_length=DEFAULT_SPEC.max_length,
                                             use_multiprocessing=False), dtype=np.float32)
