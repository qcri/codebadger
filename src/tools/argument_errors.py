"""Give MCP argument-validation failures the stable VALIDATION_ERROR code."""

from fastmcp.exceptions import ToolError, ValidationError
from fastmcp.server.middleware import Middleware
from pydantic import ValidationError as PydanticValidationError

_MAX_FIELDS = 5


def _describe(error: ValidationError) -> str:
    cause = error.__cause__
    if not isinstance(cause, PydanticValidationError):
        return str(error)
    parts = []
    for item in cause.errors()[:_MAX_FIELDS]:
        field = ".".join(str(p) for p in item.get("loc", ())) or "arguments"
        parts.append(f"{field}: {item.get('msg', 'invalid value')}")
    return "; ".join(parts)


class ArgumentValidationMiddleware(Middleware):
    """FastMCP rejects arguments that don't match a tool's input schema before the
    tool runs, with a raw pydantic message. Re-raise it as a ToolError prefixed with
    VALIDATION_ERROR so clients see the same code the tools raise themselves."""

    async def on_call_tool(self, context, call_next):
        try:
            return await call_next(context)
        except ValidationError as e:
            raise ToolError(
                f"VALIDATION_ERROR: {_describe(e)} "
                "Hint: check the tool's input schema for required fields and allowed values."
            ) from e
