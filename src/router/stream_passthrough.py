from typing import Any, AsyncIterator

from fastapi import Response
from fastapi.responses import StreamingResponse


async def prepend_async_item(first_item: Any, iterator: AsyncIterator[Any]):
    """Yield a prefetched item before continuing the original iterator."""
    yield first_item
    async for item in iterator:
        yield item


async def read_first_async_item(iterator: AsyncIterator[Any]) -> Any:
    """Python 3.9-compatible async equivalent of built-in anext()."""
    return await iterator.__anext__()


async def build_streaming_response_or_error(
    iterator: AsyncIterator[Any],
    media_type: str = "text/event-stream",
):
    """
    Prefetch the first async item so router code can return an upstream error
    response directly before FastAPI commits a 200 streaming response.
    """
    try:
        first_item = await read_first_async_item(iterator)
    except StopAsyncIteration:
        return Response(status_code=204)

    if isinstance(first_item, Response):
        return first_item

    return StreamingResponse(
        prepend_async_item(first_item, iterator),
        media_type=media_type,
    )
