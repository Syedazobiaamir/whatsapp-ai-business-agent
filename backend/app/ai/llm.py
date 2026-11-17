"""The only place that talks to Gemini.

Every AI module asks for "JSON matching this Pydantic schema". Keeping that behind one small
interface (LLMClient) means:
  * tests swap in FakeLLM: no network, no API key, no cost, deterministic results;
  * switching AI provider later = one new class, the agents do not change.
"""
import logging
from typing import Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.models.enums import ErrorType

logger = logging.getLogger("app.ai")
T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    def __init__(self, message: str, error_type: ErrorType):
        super().__init__(message)
        self.error_type = error_type


class LLMClient(Protocol):
    model: str

    async def generate_json(self, *, system: str, prompt: str, schema: type[T],
                            temperature: float) -> T: ...


def classify_api_error(code: int | None) -> ErrorType:
    """Map Gemini HTTP codes to our error types (shown on the monitoring page)."""
    if code in (401, 403):
        return ErrorType.AUTH                 # bad / missing / blocked API key
    if code == 429:
        return ErrorType.RATE_LIMIT           # free-tier quota or rate limit reached
    if code is not None and code >= 500:
        return ErrorType.TEMPORARY
    if code in (400, 404):
        return ErrorType.VALIDATION           # e.g. wrong model name
    return ErrorType.UNKNOWN


class GeminiLLM:
    def __init__(self, settings: Settings, model: str | None = None):
        from google import genai
        from google.genai import types

        self._types = types
        self.model = model or settings.gemini_model
        self._client = genai.Client(
            api_key=settings.gemini_api_key.get_secret_value(),
            # timeout is in milliseconds. The SDK itself retries 429/5xx a few times.
            http_options=types.HttpOptions(timeout=int(settings.gemini_timeout_seconds * 1000)),
        )

    async def generate_json(self, *, system: str, prompt: str, schema: type[T],
                            temperature: float) -> T:
        from google.genai import errors

        config = self._types.GenerateContentConfig(
            system_instruction=system,
            temperature=temperature,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),   # Gemini must follow this shape
        )
        last_problem = ""
        for attempt in (1, 2):    # one extra try if the JSON does not match the schema
            try:
                response = await self._client.aio.models.generate_content(
                    model=self.model, contents=prompt, config=config)
            except errors.APIError as exc:
                raise LLMError(f"Gemini API error {exc.code}: {exc.message}",
                               classify_api_error(exc.code)) from exc
            except Exception as exc:   # network errors, timeouts
                raise LLMError(f"Gemini request failed: {exc!r}", ErrorType.TEMPORARY) from exc
            try:
                # Never trust model output blindly: Pydantic validates types, ranges, enums.
                return schema.model_validate_json(response.text or "")
            except ValidationError as exc:
                last_problem = str(exc)[:500]
                logger.warning("Gemini returned invalid %s (attempt %s)", schema.__name__, attempt)
        raise LLMError(f"Gemini output did not match {schema.__name__}: {last_problem}",
                       ErrorType.VALIDATION)


class UnconfiguredLLM:
    """Used when GEMINI_API_KEY is empty: every call fails cleanly -> orchestrator fallback."""
    model = "none"

    async def generate_json(self, **_):
        raise LLMError("GEMINI_API_KEY is not set", ErrorType.AUTH)


def build_llm(settings: Settings, model: str | None = None) -> LLMClient:
    if not settings.gemini_api_key.get_secret_value():
        return UnconfiguredLLM()
    return GeminiLLM(settings, model)
