"""Registry provider LLM: provider OpenAI-compatible & Anthropic-compatible dengan credentials masing-masing.

Admin mengelola provider, model, dan penugasan (purpose × ai_role, plus override per kanal) lewat
`/api/v1/admin/llm/*`; runtime me-resolve model per request lewat `LlmRegistry`. Tanpa data di DB,
registry jatuh ke konfigurasi env lama (`LLM_*`, `EMBEDDING_*`).
"""

from ai_service.llm.registry import LlmRegistry, ResolvedModel
from ai_service.llm.spec import ModelSpec, ProviderSpec

__all__ = ["LlmRegistry", "ModelSpec", "ProviderSpec", "ResolvedModel"]
