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
    
    def write(self, offset: int, value: int):
        """
        Write to MMIO register - STESSA API di PYNQ!
        
        Parameters
        ----------
        offset : int
            Offset dal base address
        value : int
            Valore da scrivere (32-bit)
        """
        if offset < 0 or offset >= self.length:
            raise ValueError(f"Offset {offset} outside MMIO range [0, {self.length})")
            
        # Verifica che il valore sia nel range 32-bit
        if value < 0 or value > 0xFFFFFFFF:
            raise ValueError(f"Value {value} outside 32-bit range")
        
        request = pb2.MMIOWriteRequest(
            handle=self._handle,
            offset=offset,
            value=value
        )
        
        self._connection.call_with_auth('MMIOWrite', request)
        
        if self.debug:
            logger.debug(f"MMIO write: 0x{self.base_addr + offset:08x} = 0x{value:08x}")

    def write_mm(self, offset: int, data: bytes):
        """Write a block of bytes to MMIO"""
        for i in range(0, len(data), 4):
            if i + 4 <= len(data):
                value = int.from_bytes(data[i:i+4], byteorder='little')
                self.write(offset + i, value)  # NO length parameter!
            else:
                # Handle remaining bytes - but MMIO typically only supports 32-bit writes
                remaining = len(data) - i
                if remaining > 0:
                    # Pad with zeros to make a full 32-bit write
                    padded = data[i:] + b'\x00' * (4 - remaining)
                    value = int.from_bytes(padded[:4], byteorder='little')
                    self.write(offset + i, value)

    def read_mm(self, offset: int, length: int) -> bytes:
        """Read a block of bytes from MMIO"""
        data = bytearray()
        for i in range(0, length, 4):
            if i + 4 <= length:
                value = self.read(offset + i, 4)
                data.extend(value.to_bytes(4, byteorder='little'))
            else:
                # Read the last partial word
                value = self.read(offset + i, 4)  # Always read 32-bit
                remaining = length - i
                # Take only the bytes we need
                data.extend(value.to_bytes(4, byteorder='little')[:remaining])
        return bytes(data)