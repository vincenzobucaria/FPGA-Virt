# client/pynq_proxy/allocate.py
import numpy as np
import logging
import mmap
import os
from multiprocessing import shared_memory
from client.connection import Connection
import pynq_service_pb2 as pb2

logger = logging.getLogger(__name__)

class ProxyBuffer:
    """Buffer proxy con supporto char device zero-copy, shared memory, o gRPC"""
    
    def __init__(self, shape, dtype, handle: str, physical_address: int,
                 connection: Connection, shm_name: str = None, 
                 vm_offset: int = None, char_device_path: str = None):
        self._connection = connection
        self._handle = handle
        self.physical_address = physical_address
        self.shape = shape
        self.dtype = dtype
        self._closed = False
        
        # Determina quale metodo usare (priorità: char_device > shm > grpc)
        if char_device_path and vm_offset is not None:
            self._setup_char_device(char_device_path, vm_offset, shape, dtype)
        elif shm_name:
            self._setup_shared_memory(shm_name, shape, dtype)
        else:
            self._setup_local_buffer(shape, dtype)
    
    def _setup_char_device(self, device_path: str, vm_offset: int, shape, dtype):
        """Setup con char device (ZERO-COPY!)"""
        try:
            # Apri char device
            self._char_fd = os.open(device_path, os.O_RDWR | os.O_SYNC)
            
            # Calcola dimensione
            np_dtype = np.dtype(dtype)
            size = int(np.prod(shape)) * np_dtype.itemsize
            
            # mmap con offset specifico
            self._char_mmap = mmap.mmap(
                fileno=self._char_fd,
                length=size,
                flags=mmap.MAP_SHARED,
                prot=mmap.PROT_READ | mmap.PROT_WRITE,
                offset=vm_offset
            )
            
            # Crea numpy array sul mapping
            self._array = np.ndarray(shape, dtype=dtype, buffer=self._char_mmap)
            
            self._access_mode = 'char_device'
            self._shm = None
            
            logger.info(f"Buffer {self._handle} using CHAR DEVICE (ZERO-COPY) at offset 0x{vm_offset:x}")
            
        except Exception as e:
            logger.warning(f"Failed to setup char device: {e}")
            # Fallback a shared memory o locale
            if hasattr(self, '_char_fd'):
                os.close(self._char_fd)
            self._setup_local_buffer(shape, dtype)
    
    def _setup_shared_memory(self, shm_name: str, shape, dtype):
        """Setup con shared memory"""
        try:
            self._shm = shared_memory.SharedMemory(name=shm_name)
            self._array = np.ndarray(shape, dtype=dtype, buffer=self._shm.buf)
            self._access_mode = 'shared_memory'
            logger.info(f"Buffer {self._handle} using SHARED MEMORY: {shm_name}")
        except Exception as e:
            logger.warning(f"Failed to connect to shared memory: {e}")
            self._setup_local_buffer(shape, dtype)
    
    def _setup_local_buffer(self, shape, dtype):
        """Fallback a buffer locale"""
        self._shm = None
        self._array = np.zeros(shape, dtype=dtype)
        self._access_mode = 'grpc'
        self._dirty = False
        logger.info(f"Buffer {self._handle} using GRPC (fallback)")
    
    def __getitem__(self, key):
        if self._closed:
            raise ValueError("Buffer has been closed")
            
        if self._access_mode in ['char_device', 'shared_memory']:
            return self._array[key]  # Accesso diretto!
        else:
            if not self._dirty:
                self.sync_from_device()
            return self._array[key]
    
    def __setitem__(self, key, value):
        if self._closed:
            raise ValueError("Buffer has been closed")
            
        if self._access_mode in ['char_device', 'shared_memory']:
            self._array[key] = value  # Scrittura diretta!
        else:
            self._array[key] = value
            self._dirty = True
    
    def sync_to_device(self):
        """Sincronizza buffer con device"""
        if self._closed:
            return
            
        if self._access_mode == 'char_device':
            # Con char device non serve fare nulla - già tutto in memoria fisica!
            logger.debug(f"Buffer {self._handle} - char device, no sync needed")
        elif self._access_mode == 'shared_memory':
            logger.debug(f"Buffer {self._handle} - shared memory, no sync needed")
        else:
            # gRPC: invia dati
            request = pb2.WriteBufferRequest(
                handle=self._handle,
                offset=0,
                data=self._array.tobytes()
            )
            self._connection.call_with_auth('WriteBuffer', request)
            self._dirty = False
    
    def sync_from_device(self):
        """Sincronizza da device"""
        if self._closed:
            return
            
        if self._access_mode == 'char_device':
            # Con char device i dati sono già aggiornati!
            logger.debug(f"Buffer {self._handle} - char device, no sync needed")
        elif self._access_mode == 'shared_memory':
            logger.debug(f"Buffer {self._handle} - shared memory, no sync needed")
        else:
            # gRPC: leggi dati
            request = pb2.ReadBufferRequest(
                handle=self._handle,
                offset=0,
                length=self._array.nbytes
            )
            response = self._connection.call_with_auth('ReadBuffer', request)
            self._array = np.frombuffer(response.data, dtype=self.dtype).reshape(self.shape)
            self._dirty = False
    
    def close(self):
        """Cleanup"""
        if self._closed:
            return
        
        # Cleanup char device
        if self._access_mode == 'char_device':
            if hasattr(self, '_char_mmap'):
                self._char_mmap.close()
            if hasattr(self, '_char_fd'):
                os.close(self._char_fd)
        
        # Cleanup shared memory
        if self._shm:
            self._shm.close()
        
        self._closed = True
        logger.debug(f"Buffer {self._handle} closed")
    
    # Proprietà numpy-like
    @property
    def nbytes(self):
        return self._array.nbytes
    
    @property
    def size(self):
        return self._array.size
    
    def __repr__(self):
        status = "closed" if self._closed else "active"
        return f"ProxyBuffer({self._access_mode}, {status}, shape={self.shape}, dtype={self.dtype})"
    
    def freebuffer(self):
        self.close()
    
    def __del__(self):
        if hasattr(self, '_closed') and not self._closed:
            self.close()


def allocate(shape, dtype=np.uint8, target=None, **kwargs):
    """Alloca buffer - identico a pynq.allocate()"""
    connection = Connection()
    
    if isinstance(shape, int):
        shape = (shape,)
    shape_list = list(shape)
    
    request = pb2.AllocateBufferRequest(
        shape=shape_list,
        dtype=str(np.dtype(dtype))
    )
    
    response = connection.call_with_auth('AllocateBuffer', request)
    
    # Estrai parametri dal response
    vm_offset = response.vm_offset if response.HasField('vm_offset') else None
    char_device = response.char_device_path if response.HasField('char_device_path') else None
    shm_name = response.shm_name if response.HasField('shm_name') else None
    phys_addr = response.physical_address if response.HasField('physical_address') else 0
    
    return ProxyBuffer(
        shape=shape,
        dtype=dtype,
        handle=response.handle,
        physical_address=phys_addr,
        connection=connection,
        shm_name=shm_name,
        vm_offset=vm_offset,
        char_device_path=char_device
    )