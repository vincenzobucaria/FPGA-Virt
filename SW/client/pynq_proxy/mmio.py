# client/pynq_proxy/mmio.py
import os
import mmap
import numpy as np
import logging

logger = logging.getLogger(__name__)

class MMIO:
    """Memory-mapped I/O con accesso diretto via UIO device"""
    
    def __init__(self, base_addr: int, length: int = 4, uio_device: str = None, debug: bool = False):
        """
        Inizializza MMIO con accesso diretto
        
        Parameters
        ----------
        base_addr : int
            Base address (usato solo per compatibilità API)
        length : int
            Lunghezza della regione MMIO
        uio_device : str
            Path al device UIO (es. "/dev/uio0")
        debug : bool
            Abilita debug logging
        """
        if uio_device is None:
            raise ValueError("uio_device must be specified for direct access")
            
        self.base_addr = base_addr
        self.length = length
        self.debug = debug
        
        # Open UIO device
        self.fd = os.open(uio_device, os.O_RDWR | os.O_SYNC)
        
        # Memory map MMIO region
        self.mmap = mmap.mmap(
            self.fd, length, 
            mmap.MAP_SHARED,
            mmap.PROT_READ | mmap.PROT_WRITE
        )
        
        # Create numpy array for word-aligned access
        self.array = np.frombuffer(self.mmap, dtype=np.uint32)
        
        if self.debug:
            logger.debug(f"MMIO mapped to {uio_device} at 0x{base_addr:08x}")
    
    def read(self, offset: int = 0, length: int = 4) -> int:
        """Read from MMIO register"""
        if offset % 4 != 0:
            raise ValueError("Offset must be 4-byte aligned")
        if offset + length > self.length:
            raise ValueError(f"Access outside MMIO range")
            
        idx = offset >> 2
        value = int(self.array[idx])
        
        if self.debug:
            logger.debug(f"MMIO read: 0x{self.base_addr + offset:08x} = 0x{value:08x}")
            
        return value & ((1 << (8 * length)) - 1)
    
    def write(self, offset: int, value: int):
        """Write to MMIO register"""
        if offset % 4 != 0:
            raise ValueError("Offset must be 4-byte aligned")
        if offset >= self.length:
            raise ValueError(f"Offset outside MMIO range")
            
        idx = offset >> 2
        self.array[idx] = np.uint32(value)
        
        if self.debug:
            logger.debug(f"MMIO write: 0x{self.base_addr + offset:08x} = 0x{value:08x}")
    
    def close(self):
        """Cleanup resources"""
        if hasattr(self, 'mmap'):
            self.mmap.close()
        if hasattr(self, 'fd'):
            os.close(self.fd)
    
    def __del__(self):
        try:
            self.close()
        except:
            pass