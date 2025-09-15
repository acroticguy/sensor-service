"""
Custom wrapper for OpenPyLivox that disables internal data capture
This prevents buffer size errors and allows us to capture data directly
"""
import sys
import os
import socket
import time
import threading
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '../../openpylivox-master'))
import openpylivox as opl

logger = logging.getLogger('lidar_service')


class DummyMonitoringThread:
    """Dummy monitoring thread that does nothing"""
    def __init__(self):
        self.started = False
        self.thread = None
        
    def stop(self):
        self.started = False
        
    def start(self):
        pass


class LivoxSensorWrapper:
    """Wrapper that uses OpenPyLivox for control but captures data directly"""
    
    def __init__(self, sensor: opl.openpylivox):
        self.sensor = sensor
        self._prevent_internal_capture()
        
    def _prevent_internal_capture(self):
        """Prevent OpenPyLivox from binding to data ports"""
        # First, try to stop any existing monitoring thread
        if hasattr(self.sensor, '_monitoringThread') and self.sensor._monitoringThread:
            try:
                # Access the actual thread object
                if hasattr(self.sensor._monitoringThread, 'started'):
                    self.sensor._monitoringThread.started = False
                if hasattr(self.sensor._monitoringThread, 't_socket'):
                    try:
                        self.sensor._monitoringThread.t_socket.close()
                    except:
                        pass
                if hasattr(self.sensor._monitoringThread, 'thread') and self.sensor._monitoringThread.thread:
                    # Don't join, just mark as not alive
                    self.sensor._monitoringThread.thread = None
            except Exception as e:
                logger.debug(f"Error stopping monitoring thread: {e}")
        
        # Replace with dummy thread that does nothing
        self.sensor._monitoringThread = DummyMonitoringThread()
        
        # Close data socket if it exists
        if hasattr(self.sensor, '_dataSocket') and self.sensor._dataSocket:
            try:
                self.sensor._dataSocket.close()
                self.sensor._dataSocket = None
            except:
                pass
                
        # Override dataStart methods to prevent internal capture
        self.sensor.dataStart = self._dummy_data_start
        self.sensor._dataStart_RT = self._dummy_data_start
        self.sensor._dataStart_RT_B = self._dummy_data_start
        self.sensor.dataStart_RT = self._dummy_data_start
        self.sensor.dataStart_RT_B = self._dummy_data_start
        
        # Prevent any new monitoring thread from being created
        def dummy_monitor(*args, **kwargs):
            return DummyMonitoringThread()
        
        # Override the monitoring thread class if it exists
        if hasattr(opl, '_monitoringThread'):
            opl._monitoringThread = dummy_monitor
        
    def _dummy_data_start(self):
        """Dummy method that doesn't start internal capture"""
        logger.debug("Bypassed OpenPyLivox internal data capture")
        return True
        
    def send_start_command(self):
        """Send start data command without starting internal capture"""
        if self.sensor._isConnected:
            try:
                self.sensor._waitForIdle()
                self.sensor._cmdSocket.sendto(self.sensor._CMD_DATA_START, (self.sensor._sensorIP, 65000))
                self.sensor._isData = True
                logger.info(f"Sent start data command to {self.sensor._sensorIP}")
                return True
            except Exception as e:
                logger.error(f"Error sending start command: {e}")
                return False
        return False
        
    def send_stop_command(self):
        """Send stop data command"""
        if self.sensor._isConnected:
            try:
                self.sensor._waitForIdle()
                self.sensor._cmdSocket.sendto(self.sensor._CMD_DATA_STOP, (self.sensor._sensorIP, 65000))
                self.sensor._isData = False
                logger.info(f"Sent stop data command to {self.sensor._sensorIP}")
                return True
            except Exception as e:
                logger.error(f"Error sending stop command: {e}")
                return False
        return False
    
    def get_data_port(self):
        """Get the data port for direct UDP capture"""
        conn_params = self.sensor.connectionParameters()
        if isinstance(conn_params[2], list):
            return int(conn_params[2][0])
        else:
            return int(conn_params[2])
    
    def lidarSpinDown(self):
        """Safely spin down the lidar"""
        try:
            # First stop monitoring thread to prevent errors
            if hasattr(self.sensor, '_monitoringThread') and self.sensor._monitoringThread:
                self.sensor._monitoringThread.started = False
                if hasattr(self.sensor._monitoringThread, 't_socket'):
                    try:
                        self.sensor._monitoringThread.t_socket.close()
                    except:
                        pass
            # Then spin down
            return self.sensor.lidarSpinDown()
        except Exception as e:
            logger.error(f"Error spinning down: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from sensor safely"""
        try:
            # Stop monitoring thread first
            if hasattr(self.sensor, '_monitoringThread') and self.sensor._monitoringThread:
                self.sensor._monitoringThread.started = False
                # Close the socket to prevent the error
                if hasattr(self.sensor._monitoringThread, 't_socket'):
                    try:
                        self.sensor._monitoringThread.t_socket.close()
                    except:
                        pass
            # Then disconnect
            return self.sensor.disconnect()
        except:
            return False
    
    def __getattr__(self, name):
        """Delegate all other attributes to the wrapped sensor"""
        return getattr(self.sensor, name)