"""Compat shims for Haystack's OpenAI-flavored embedders.

Haystack's `OpenAIDocumentEmbedder` and `OpenAITextEmbedder` both call
`dict(response.usage)` on the embeddings response, which crashes when the
provider omits the `usage` field. Google Gemini's OpenAI-compat endpoint
omits it; OpenRouter and OpenAI itself include it.

These thin subclasses substitute an empty usage dict when the provider
returns null, so the same Haystack pipeline code works against any
OpenAI-compatible embeddings endpoint.

Drop-in replacements wherever the originals are imported.
"""

from typing import Any

from haystack import component
from haystack.components.embedders import (
    OpenAIDocumentEmbedder as _OpenAIDocumentEmbedder,
)
from haystack.components.embedders import (
    OpenAITextEmbedder as _OpenAITextEmbedder,
)

_EMPTY_USAGE = {"prompt_tokens": 0, "total_tokens": 0}


def _usage_dict(response_usage: Any) -> dict:
    """Return a dict for response.usage, tolerating None."""
    if response_usage is None:
        return dict(_EMPTY_USAGE)
    return dict(response_usage)


class TolerantDocumentEmbedder(_OpenAIDocumentEmbedder):
    """OpenAIDocumentEmbedder that tolerates providers omitting `usage`."""

    def _embed_batch(
        self, texts_to_embed: dict[str, str], batch_size: int
    ) -> tuple[dict[str, list[float]], dict[str, Any]]:
        from itertools import islice

        from openai import APIError
        from tqdm import tqdm

        def _batched(iterable, size):
            it = iter(iterable)
            while batch := list(islice(it, size)):
                yield batch

        doc_ids_to_embeddings: dict[str, list[float]] = {}
        meta: dict[str, Any] = {}
        for batch in tqdm(
            _batched(texts_to_embed.items(), batch_size),
            disable=not self.progress_bar,
            desc="Calculating embeddings",
        ):
            args: dict[str, Any] = {
                "model": self.model,
                "input": [b[1] for b in batch],
                "encoding_format": "float",
            }
            if self.dimensions is not None:
                args["dimensions"] = self.dimensions

            try:
                response = self.client.embeddings.create(**args)
            except APIError as exc:
                if self.raise_on_failure:
                    raise exc
                continue

            embeddings = [el.embedding for el in response.data]
            doc_ids_to_embeddings.update(dict(zip((b[0] for b in batch), embeddings, strict=True)))

            if "model" not in meta:
                meta["model"] = response.model
            usage = _usage_dict(response.usage)
            if "usage" not in meta:
                meta["usage"] = usage
            else:
                meta["usage"]["prompt_tokens"] += usage.get("prompt_tokens", 0)
                meta["usage"]["total_tokens"] += usage.get("total_tokens", 0)

        return doc_ids_to_embeddings, meta


class TolerantTextEmbedder(_OpenAITextEmbedder):
    """OpenAITextEmbedder that tolerates providers omitting `usage`."""

    @component.output_types(embedding=list[float], meta=dict[str, Any])
    def run(self, text: str):
        if not isinstance(text, str):
            raise TypeError(
                "OpenAITextEmbedder expects a string as an input. "
                "If you want to embed a list of Documents, use the OpenAIDocumentEmbedder."
            )

        text_to_embed = self.prefix + text + self.suffix

        if self.dimensions is not None:
            response = self.client.embeddings.create(
                model=self.model,
                dimensions=self.dimensions,
                input=text_to_embed,
            )
        else:
            response = self.client.embeddings.create(model=self.model, input=text_to_embed)

        return {
            "embedding": response.data[0].embedding,
            "meta": {"model": response.model, "usage": _usage_dict(response.usage)},
        }
