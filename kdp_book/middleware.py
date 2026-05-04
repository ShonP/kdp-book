"""MAF middleware: chat-call logging, token tracking, retries, prompt caching.

Skeleton — full implementation in Phase 0. Same shape as
deep-research/middleware.py and mangas/middleware/logging_middleware.py:
`@chat_middleware` and `@function_middleware` decorators that emit one
structured log per agent invocation and aggregate token usage into a
`TokenUsage` singleton.
"""

from __future__ import annotations

# Implementation in Phase 0:
#
#   class TokenUsage(BaseModel): ...
#   def get_token_usage() -> TokenUsage: ...
#   def reset_token_usage() -> None: ...
#
#   @chat_middleware
#   async def llm_call_logging(context: ChatContext, next_): ...
#
#   ALL_MIDDLEWARE = [llm_call_logging, ...]
