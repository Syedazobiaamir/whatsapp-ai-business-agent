"""The two AI modules that call Gemini. Each has ONE job and returns validated, structured data.

They do not touch the database or send messages: the orchestrator decides what to do with
their output. That keeps them easy to test and easy to reason about.
"""
from app.ai.context import AgentContext
from app.ai.llm import LLMClient
from app.ai.prompts import sales_prompt, sales_system, support_prompt, support_system
from app.ai.schemas import SalesResult, SupportResult


def canonical_services(names: list[str], ctx: AgentContext) -> list[str]:
    """Keep only names that exist in our catalog, spelled exactly as in the catalog."""
    catalog = {s.name.lower(): s.name for s in ctx.services}
    seen: list[str] = []
    for n in names:
        real = catalog.get((n or "").strip().lower())
        if real and real not in seen:
            seen.append(real)
    return seen


class SupportAgent:
    """A. Customer Support: intent detection + FAQ answers + 'does this need a human?'."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def run(self, ctx: AgentContext) -> SupportResult:
        result = await self.llm.generate_json(
            system=support_system(ctx), prompt=support_prompt(ctx), schema=SupportResult,
            temperature=float(ctx.ai_config.get("temperature", 0.3)),
        )
        provided = {f.id for f in ctx.faqs}
        result.used_faq_ids = [i for i in result.used_faq_ids if i in provided]   # no invented ids
        return result


class SalesAgent:
    """B. Sales: answer + one qualifying question + extract lead facts + recommend services."""

    def __init__(self, llm: LLMClient):
        self.llm = llm

    async def run(self, ctx: AgentContext, intent: str) -> SalesResult:
        result = await self.llm.generate_json(
            system=sales_system(ctx), prompt=sales_prompt(ctx, intent), schema=SalesResult,
            temperature=float(ctx.ai_config.get("temperature", 0.3)),
        )
        result.recommended_services = canonical_services(result.recommended_services, ctx)
        result.facts.interested_services = canonical_services(result.facts.interested_services, ctx)
        return result
