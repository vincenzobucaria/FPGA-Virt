# client/pynq_proxy/allocate.py - VERSIONE CON CLEANUP
import numpy as np
import logging
from multiprocessing import shared_memory
from client.connection import Connection
import pynq_service_pb2 as pb2

logger = logging.getLogger(__name__)

class ProxyBuffer:
    """Buffer proxy che usa shared memory quando disponibile con cleanup automatico"""
    
    def __init__(self, shape, dtype, handle: str, physical_address: int,
                 connection: Connection, shm_name: str = None):
        self._connection = connection
        self._handle = handle
        self.physical_address = physical_address
        self.shape = shape
        self.dtype = dtype
        self._shm_name = shm_name
        self._closed = False
        
        # Prova a connettersi a shared memory
        if shm_name:
            try:
                self._shm = shared_memory.SharedMemory(name=shm_name)
                self._array = np.ndarray(shape, dtype=dtype, buffer=self._shm.buf)
                self._use_shm = True
                logger.info(f"Connected to shared memory: {shm_name}")
            except Exception as e:
                logger.warning(f"Failed to connect to shared memory: {e}")
                self._setup_local_buffer()
        else:
            self._setup_local_buffer()
    
    def _setup_local_buffer(self):
        """Fallback a buffer locale"""
        self._shm = None
        self._array = np.zeros(self.shape, dtype=self.dtype)
        self._use_shm = False
        self._dirty = False  # Track se buffer locale è stato modificato
    
    def __getitem__(self, key):
        """Get item - da shared memory o locale"""
        if self._closed:
            raise ValueError("Buffer has been closed")
            
        if self._use_shm:
            return self._array[key]  # Diretto da shared memory!
        else:
            # Con buffer locale, potremmo dover sincronizzare prima
            if not self._dirty:
                # Prima lettura: sincronizza da device
                self.sync_from_device()
            return self._array[key]
    
    def __setitem__(self, key, value):
        """Set item - su shared memory o locale"""
        if self._closed:
            raise ValueError("Buffer has been closed")
            
        if self._use_shm:
            self._array[key] = value  # Diretto su shared memory!
        else:
            self._array[key] = value
            self._dirty = True
    
    def sync_to_device(self):
        """Sincronizza buffer con device"""
        if self._closed:
            return
            
        if self._use_shm:
            # Con shared memory non serve fare nulla!
            # Il server vede già le modifiche
            logger.debug(f"Buffer {self._handle} already synced (shared memory)")
        else:
            # Invia dati al server via gRPC
            request = pb2.WriteBufferRequest(
                handle=self._handle,
                offset=0,
                data=self._array.tobytes()
            )
            self._connection.call_with_auth('WriteBuffer', request)
            self._dirty = False
            logger.debug(f"Buffer {self._handle} synced to device via gRPC")
    
    def sync_from_device(self):
        """Sincronizza da device"""
        if self._closed:
            return
            
        if self._use_shm:
            # Con shared memory i dati sono già aggiornati
            logger.debug(f"Buffer {self._handle} already synced (shared memory)")
        else:
            # Leggi da server via gRPC
            request = pb2.ReadBufferRequest(
                handle=self._handle,
                offset=0,
                length=self._array.nbytes
            )
            response = self._connection.call_with_auth('ReadBuffer', request)
            self._array = np.frombuffer(response.data, dtype=self.dtype).reshape(self.shape)
            self._dirty = False
            logger.debug(f"Buffer {self._handle} synced from device via gRPC")
    
    def close(self):
        """Dealloca buffer"""
        if self._closed:
            return
            
        # Cleanup shared memory se usata
        if self._shm:
            self._shm.close()
        
        # Nota: NON deallochiamo singolarmente il buffer
        # Il cleanup generale avverrà tramite Connection.cleanup_resources()
        # Questo evita problemi se il buffer è ancora in uso da DMA, etc.
        
        self._closed = True
        logger.debug(f"Buffer {self._handle} marked as closed")
    
    # Proprietà numpy-like
    @property
    def nbytes(self):
        return self._array.nbytes
    
    @property
    def size(self):
        return self._array.size
    
    def __repr__(self):
        mode = "shared-memory" if self._use_shm else "grpc"
        status = "closed" if self._closed else "active"
        return f"ProxyBuffer({mode}, {status}, shape={self.shape}, dtype={self.dtype}, handle={self._handle[:8]}...)"
    
    # Compatibilità con pynq.Buffer
    def freebuffer(self):
        """Alias per close()"""
        self.close()
    
    def __del__(self):
        """Distruttore - assicura cleanup"""
        if hasattr(self, '_closed') and not self._closed:
            self.close()
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()


def allocate(shape, dtype=np.uint8, target=None, **kwargs):
    """Alloca buffer compatibile con pynq.allocate"""
    connection = Connection()
    
    # Normalizza shape
    if isinstance(shape, int):
        shape = (shape,)
    shape_list = list(shape)
    
    # Prepara richiesta - AGGIORNATA per nuovo proto
    request = pb2.AllocateBufferRequest(
        shape=shape_list,
        dtype=str(np.dtype(dtype))
    )
    
    response = connection.call_with_auth('AllocateBuffer', request)
    
    # Crea ProxyBuffer con shared memory se disponibile
    return ProxyBuffer(
        shape=shape,
        dtype=dtype,
        handle=response.handle,
        physical_address=response.physical_address if response.HasField('physical_address') else 0,
        connection=connection,
        shm_name=response.shm_name if response.HasField('shm_name') else None
    )