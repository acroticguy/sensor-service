import time
import logging
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

api_logger = logging.getLogger("api_requests")


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start_time = time.time()
        
        body = await request.body()
        
        response = await call_next(request)
        
        process_time = time.time() - start_time
        
        api_logger.info(
            f"{request.method} {request.url.path} - "
            f"Status: {response.status_code} - "
            f"Duration: {process_time:.3f}s - "
            f"Client: {request.client.host if request.client else 'Unknown'}"
        )
        
        response.headers["X-Process-Time"] = str(process_time)
        
        return response