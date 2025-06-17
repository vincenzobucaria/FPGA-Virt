# client/pynq_proxy/mmio.py
import logging
import sys
from client.connection import Connection
import pynq_service_pb2 as pb2

logger = logging.getLogger(__name__)

class MMIO:
    """Memory-mapped I/O proxy - API compatibile con pynq.mmio.MMIO"""
    
    def __init__(self, base_addr: int, length: int = 4, debug: bool = False):
        """
        Inizializza MMIO - STESSA API di PYNQ!
        
        Parameters
        ----------
        base_addr : int
            Base address del registro MMIO
        length : int
            Lunghezza della regione MMIO in bytes
        debug : bool
            Abilita debug logging
        """
        self._connection = Connection()
        self.base_addr = base_addr
        self.length = length
        self.debug = debug
        
        # Per il proxy, dobbiamo creare un handle sul server
        # MA questo è trasparente all'utente
        self._create_server_handle()
        
    def _create_server_handle(self):
        """Crea handle MMIO sul server (interno, non esposto)"""
        # Dobbiamo passare overlay_id e ip_name, ma come?
        # Soluzione: il server può dedurli dall'indirizzo!
        
        request = pb2.CreateMMIORequest(
            overlay_id="current",  # Server userà l'overlay corrente del tenant
            ip_name="direct_mmio",  # Nome generico per MMIO creati direttamente
            base_address=self.base_addr,
            length=self.length
        )
        
        response = self._connection.call_with_auth('CreateMMIO', request)
        self._handle = response.handle
        
        if self.debug:
            logger.debug(f"Created MMIO at 0x{self.base_addr:08x} with handle: {self._handle}")
    
    def read(self, offset: int = 0, length: int = 4) -> int:
        """
        Read from MMIO register - STESSA API di PYNQ!
        
        Parameters
        ----------
        offset : int
            Offset dal base address
        length : int  
            Numero di bytes da leggere (1, 2, 4, o 8)
            
        Returns
        -------
        int
            Valore letto
        """
        if offset + length > self.length:
            raise ValueError(f"Accessing outside MMIO range: {offset} + {length} > {self.length}")
            
        request = pb2.MMIOReadRequest(
            handle=self._handle,
            offset=offset,
            length=length
        )
        
        response = self._connection.call_with_auth('MMIORead', request)
        
        if self.debug:
            logger.debug(f"MMIO read: 0x{self.base_addr + offset:08x} = 0x{response.value:08x}")
            
        return response.value
    
    def write(self, offset: int, value: int, length: int = 4):
        """
        Write to MMIO register - STESSA API di PYNQ!
        
        Parameters
        ----------
        offset : int
            Offset dal base address
        value : int
            Valore da scrivere
        length : int
            Numero di bytes da scrivere (1, 2, 4, o 8)
        """
        if offset + length > self.length:
            raise ValueError(f"Accessing outside MMIO range: {offset} + {length} > {self.length}")
            
        request = pb2.MMIOWriteRequest(
            handle=self._handle,
            offset=offset,
            value=value
        )
        
        self._connection.call_with_auth('MMIOWrite', request)
        
        if self.debug:
            logger.debug(f"MMIO write: 0x{self.base_addr + offset:08x} = 0x{value:08x}")
    
    # Metodi helper per compatibilità PYNQ
    def write_mm(self, offset: int, data: bytes):
        """Write a block of bytes to MMIO"""
        for i in range(0, len(data), 4):
            if i + 4 <= len(data):
                value = int.from_bytes(data[i:i+4], byteorder='little')
                self.write(offset + i, value, 4)
            else:
                # Handle remaining bytes
                remaining = len(data) - i
                value = int.from_bytes(data[i:i+remaining], byteorder='little')
                self.write(offset + i, value, remaining)
    
    def read_mm(self, offset: int, length: int) -> bytes:
        """Read a block of bytes from MMIO"""
        data = bytearray()
        for i in range(0, length, 4):
            if i + 4 <= length:
                value = self.read(offset + i, 4)
                data.extend(value.to_bytes(4, byteorder='little'))
            else:
                remaining = length - i
                value = self.read(offset + i, remaining)
                data.extend(value.to_bytes(remaining, byteorder='little'))
        return bytes(data)