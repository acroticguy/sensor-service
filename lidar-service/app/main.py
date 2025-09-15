from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from contextlib import asynccontextmanager

from .core.config import settings
from .core.logging_config import logger
from .api.endpoints import connection, control, streaming, websocket, berthing_calibration, calibration_adjust, precision_config, berthing_data, modbus
from .api.middleware.logging_middleware import LoggingMiddleware
from .api.middleware.error_handler import (
    http_exception_handler,
    validation_exception_handler,
    general_exception_handler
)
from .services.device_manager import device_manager
from .services.db_streamer_service import db_streamer_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and shutdown events"""
    logger.info("Starting Livox LiDAR Service...")
    
    if settings.AUTO_CONNECT_ON_STARTUP:
        logger.info("Auto-connecting to sensors...")
        try:
            count = await device_manager.auto_connect_lidars(settings.COMPUTER_IP)
            logger.info(f"Auto-connected to {count} sensors")

            # Do NOT automatically start streaming - only connect
            # Streaming will be initiated manually via API endpoints

        except Exception as e:
            logger.error(f"Error during auto-connect: {str(e)}")

    logger.info("Starting global berthing data WebSocket emitter...")
    websocket.manager.start_all_berthing_data_stream()
    
    yield
    
    logger.info("Shutting down Livox LiDAR Service...")

    try:
        await device_manager.shutdown_devices()

        if settings.AUTO_UPDATE_DB:
            logger.info("AUTO_UPDATE_DB was active. Stopping database streaming...")
            await db_streamer_service.stop_db_streaming()

        logger.info("Stopping global berthing data WebSocket emitter...")
        await websocket.manager.stop_all_berthing_data_stream()

    except Exception as e:
        logger.error(f"Error during shutdown: {str(e)}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    description=settings.DESCRIPTION,
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

app.add_middleware(LoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

app.include_router(connection.router, prefix="/api/v1")
app.include_router(control.router, prefix="/api/v1")
app.include_router(streaming.router, prefix="/api/v1")
app.include_router(websocket.router)
app.include_router(berthing_calibration.router, prefix="/api/v1/berthing")
app.include_router(calibration_adjust.router, prefix="/api/v1/calibration")
app.include_router(precision_config.router, prefix="/api/v1/precision")
app.include_router(berthing_data.router, prefix="/api/v1/berthing/data")
app.include_router(modbus.router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint"""
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "description": settings.DESCRIPTION,
        "docs": "/docs",
        "redoc": "/redoc"
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    sensors_count = len(device_manager.lidar_manager.sensors) if device_manager.lidar_manager else 0
    streaming_count = sum(1 for active in device_manager.lidar_manager.stream_active.values() if active) if device_manager.lidar_manager else 0

    return {
        "status": "healthy",
        "sensors_connected": sensors_count,
        "sensors_streaming": streaming_count
    }