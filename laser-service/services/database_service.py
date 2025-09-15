import aiohttp
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from core.logger import get_logger

logger = get_logger(__name__)

class DatabaseService:
    def __init__(self, base_url: str = "https://api.navitrak.ai"):
        """Initialize database service with PostgREST API base URL"""
        self.base_url = base_url.rstrip('/')
        self.session: Optional[aiohttp.ClientSession] = None
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()
    
    async def ensure_session(self):
        """Ensure session exists and is not closed"""
        if not self.session or self.session.closed:
            if self.session and self.session.closed:
                logger.debug("Session was closed, creating new session")
            self.session = aiohttp.ClientSession()
    
    async def get_lasers(self, laser_type_id: int = 1, enabled: bool = True) -> List[Dict[str, Any]]:
        """
        Get all laser devices from database
        
        Args:
            laser_type_id: Filter by laser type (default 1)
            enabled: Filter by enabled status (default True)
        
        Returns:
            List of laser device configurations
        """
        await self.ensure_session()
        
        params = {
            'laser_type_id': f'eq.{laser_type_id}',
            'enabled': f'eq.{enabled}'
        }
        
        try:
            async with self.session.get(f"{self.base_url}/lasers", params=params) as response:
                if response.status == 200:
                    lasers = await response.json()
                    logger.info(f"Retrieved {len(lasers)} laser devices from database")
                    return lasers
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to get lasers: {response.status} - {error_text}")
                    return []
        except Exception as e:
            logger.error(f"Error getting lasers from database: {str(e)}")
            return []
    
    async def update_laser_online_status(self, laser_id: int, online: bool) -> bool:
        """
        Update laser online status in database
        
        Args:
            laser_id: Laser device ID
            online: Online status
            
        Returns:
            Success status
        """
        await self.ensure_session()
        
        data = {'online': online}
        
        try:
            async with self.session.patch(
                f"{self.base_url}/lasers",
                params={'laser_id': f'eq.{laser_id}'},
                json=data,
                headers={'Content-Type': 'application/json'}
            ) as response:
                if response.status == 204:
                    logger.info(f"Updated laser {laser_id} online status to {online}")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to update laser status: {response.status} - {error_text}")
                    return False
        except Exception as e:
            logger.error(f"Error updating laser status: {str(e)}")
            return False
    
    async def insert_laser_data(
        self, 
        laser_id: int, 
        dt: datetime, 
        distance: float, 
        speed: float, 
        temperature: float, 
        strength: int,
        berthing_id: Optional[int] = None,
        max_retries: int = 3
    ) -> bool:
        """
        Insert laser measurement data using PostgREST RPC function
        
        Args:
            laser_id: Laser device ID
            dt: Timestamp
            distance: Distance measurement
            speed: Speed measurement 
            temperature: Temperature measurement
            strength: Signal strength
            berthing_id: Optional berthing ID
            max_retries: Maximum number of retry attempts
            
        Returns:
            Success status
        """
        data = {
            'p_laser_id': laser_id,
            'p_dt': dt.isoformat(),
            'p_distance': distance,
            'p_speed': speed,
            'p_temperature': temperature,
            'p_strength': strength
        }
        
        if berthing_id is not None:
            data['p_berthing_id'] = berthing_id
        
        for attempt in range(max_retries):
            try:
                await self.ensure_session()
                
                async with self.session.post(
                    f"{self.base_url}/rpc/nv_insert_laser_data",
                    json=data,
                    headers={'Content-Type': 'application/json'},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        logger.debug(f"Inserted laser data for laser {laser_id}")
                        return True
                    else:
                        error_text = await response.text()
                        logger.error(f"Failed to insert laser data (attempt {attempt + 1}): {response.status} - {error_text}")
                        
            except aiohttp.ClientError as e:
                logger.error(f"HTTP error inserting laser data (attempt {attempt + 1}): {str(e)}")
                # Close and recreate session on HTTP errors
                if self.session and not self.session.closed:
                    await self.session.close()
                self.session = None
                
            except Exception as e:
                logger.error(f"Error inserting laser data (attempt {attempt + 1}): {str(e)}")
                
            # Wait before retry
            if attempt < max_retries - 1:
                await asyncio.sleep(0.5 * (attempt + 1))
        
        return False
    
    async def close_session(self):
        """Close the current session"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.debug("Database service session closed")
        self.session = None
    
    async def get_active_berthing(self, berth_id: int) -> Optional[Dict[str, Any]]:
        """
        Get active berthing for a berth
        
        Args:
            berth_id: Berth ID
            
        Returns:
            Active berthing data or None
        """
        await self.ensure_session()
        
        params = {
            'berth_id': f'eq.{berth_id}',
            'status': 'eq.active',
            'limit': '1'
        }
        
        try:
            async with self.session.get(f"{self.base_url}/berthings", params=params) as response:
                if response.status == 200:
                    berthings = await response.json()
                    return berthings[0] if berthings else None
                else:
                    return None
        except Exception as e:
            logger.error(f"Error getting active berthing: {str(e)}")
            return None