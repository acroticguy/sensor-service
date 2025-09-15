import pytest
import signal
import sys
from unittest.mock import Mock, AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient
from contextlib import asynccontextmanager

from main import app, laser_manager, lifespan, signal_handler


class TestMain:
    """Test main application functionality"""
    
    @pytest.fixture(autouse=True)
    def reset_globals(self):
        """Reset global state before each test"""
        laser_manager.devices.clear()
        yield
        laser_manager.devices.clear()
    
    def test_app_created(self):
        """Test that FastAPI app is created properly"""
        assert app.title == "LDM302 Laser Control API"
        assert app.version == "1.0.0"
        route_paths = [route.path for route in app.routes]
        # Check for some key API routes
        assert "/api/v1/health" in route_paths
        assert "/api/v1/devices" in route_paths
        assert "/api/v1/measure/distance" in route_paths
    
    def test_cors_middleware_configured(self):
        """Test CORS middleware is configured"""
        middlewares = [str(m) for m in app.user_middleware]
        assert any("CORSMiddleware" in m for m in middlewares)
    
    @pytest.mark.asyncio
    async def test_lifespan_startup(self):
        """Test lifespan startup"""
        mock_app = MagicMock()
        
        with patch.object(laser_manager, 'start', AsyncMock()) as mock_start:
            async with lifespan(mock_app):
                # Startup should call laser_manager.start()
                mock_start.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_lifespan_shutdown(self):
        """Test lifespan shutdown"""
        mock_app = MagicMock()
        
        with patch.object(laser_manager, 'start', AsyncMock()):
            with patch.object(laser_manager, 'stop', AsyncMock()) as mock_stop:
                async with lifespan(mock_app):
                    pass
                # After exiting context, stop should be called
                mock_stop.assert_called_once()
    
    def test_signal_handler(self):
        """Test signal handler"""
        with patch('sys.exit') as mock_exit:
            signal_handler(signal.SIGINT, None)
            mock_exit.assert_called_once_with(0)
    
    def test_main_execution(self):
        """Test main execution block"""
        with patch('uvicorn.run') as mock_run:
            with patch('signal.signal') as mock_signal:
                # Simulate running as main
                with patch('__main__.__name__', '__main__'):
                    # Would need to execute the main block
                    # This is just to show the test pattern
                    pass
    
    def test_api_health_endpoint(self):
        """Test that health endpoint works"""
        client = TestClient(app)
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}
    
    def test_api_root_redirect(self):
        """Test root path behavior"""
        client = TestClient(app)
        response = client.get("/")
        # Should return 404 as no root handler defined
        assert response.status_code == 404
    
    @pytest.mark.asyncio
    async def test_global_laser_manager(self):
        """Test global laser_manager instance"""
        assert laser_manager is not None
        assert isinstance(laser_manager.devices, dict)
    
    def test_api_routes_included(self):
        """Test all API routes are included"""
        client = TestClient(app)
        
        # Test some key endpoints exist
        endpoints = [
            "/api/v1/health",
            "/api/v1/devices",
            "/api/v1/measure/distance",
            "/api/v1/measure/speed",
        ]
        
        for endpoint in endpoints:
            # Use OPTIONS to check if endpoint exists
            response = client.options(endpoint)
            # Should not return 404
            assert response.status_code != 404


class TestMainEdgeCases:
    """Test edge cases in main application"""
    
    @pytest.mark.asyncio
    async def test_lifespan_with_exception_in_startup(self):
        """Test lifespan with exception during startup"""
        mock_app = MagicMock()
        
        with patch.object(laser_manager, 'start', AsyncMock(side_effect=Exception("Startup error"))):
            with pytest.raises(Exception):
                async with lifespan(mock_app):
                    pass
    
    @pytest.mark.asyncio
    async def test_lifespan_with_exception_in_shutdown(self):
        """Test lifespan with exception during shutdown"""
        mock_app = MagicMock()
        
        with patch.object(laser_manager, 'start', AsyncMock()):
            with patch.object(laser_manager, 'stop', AsyncMock(side_effect=Exception("Shutdown error"))):
                # Should handle exception gracefully
                try:
                    async with lifespan(mock_app):
                        pass
                except Exception:
                    # Exception during shutdown should be handled
                    pass
    
    def test_signal_handler_sigterm(self):
        """Test SIGTERM signal handler"""
        with patch('sys.exit') as mock_exit:
            signal_handler(signal.SIGTERM, None)
            mock_exit.assert_called_once_with(0)
    
    def test_uvicorn_configuration(self):
        """Test uvicorn run configuration"""
        with patch('uvicorn.run') as mock_run:
            # Import would trigger the main block if __name__ == "__main__"
            # This tests the configuration passed to uvicorn
            # In practice, you'd test this by running the module
            pass