# client/pynq_proxy/allocate.py
import numpy as np
import logging
import sys
from client.connection import Connection
import pynq_service_pb2 as pb2

logger = logging.getLogger(__name__)

class ProxyBuffer:
    """Buffer proxy che simula pynq.Buffer"""
    
    def __init__(self, shape, dtype, handle: str, physical_address: int, 
                 connection: Connection):
        self._connection = connection
        self._handle = handle
        self.physical_address = physical_address
        self.shape = shape
        self.dtype = dtype
        
        # Crea array numpy locale
        self._array = np.zeros(shape, dtype=dtype)
        
    def __getitem__(self, key):
        """Get item - per ora ritorna dal buffer locale"""
        return self._array[key]
        
    def __setitem__(self, key, value):
        """Set item - aggiorna buffer locale"""
        self._array[key] = value
        
    def sync_to_device(self):
        """Sincronizza buffer con device"""
        # Invia dati al server
        request = pb2.WriteBufferRequest(
            handle=self._handle,
            offset=0,
            data=self._array.tobytes()
        )
        
        self._connection.call_with_auth('WriteBuffer', request)
        logger.debug(f"Buffer {self._handle} synced to device")
        
    def sync_from_device(self):
        """Sincronizza da device"""
        request = pb2.ReadBufferRequest(
            handle=self._handle,
            offset=0,
            length=self._array.nbytes
        )
        
        response = self._connection.call_with_auth('ReadBuffer', request)
        
        # Aggiorna array locale
        self._array = np.frombuffer(response.data, dtype=self.dtype).reshape(self.shape)
        logger.debug(f"Buffer {self._handle} synced from device")
        
    def close(self):
        """Dealloca buffer"""
        request = pb2.FreeBufferRequest(handle=self._handle)
        self._connection.call_with_auth('FreeBuffer', request)
        
    # Compatibilità con pynq.Buffer
    def freebuffer(self):
        """Alias per close()"""
        self.close()

def allocate(shape, dtype=np.uint8, target=None, **kwargs):
    """Alloca buffer compatibile con pynq.allocate"""
    connection = Connection()
    
    # Calcola size in bytes
    if isinstance(shape, int):
        shape = (shape,)
    
    size = np.prod(shape) * np.dtype(dtype).itemsize
    
    # Mappa dtype numpy a buffer type
    buffer_type = 0  # Normal buffer
    
    request = pb2.AllocateBufferRequest(
        size=size,
        buffer_type=buffer_type,
        name=kwargs.get('name', '')
    )
    
    response = connection.call_with_auth('AllocateBuffer', request)
    
    return ProxyBuffer(
        shape=shape,
        dtype=dtype,
        handle=response.handle,
        physical_address=response.physical_address,
        connection=connection
    )