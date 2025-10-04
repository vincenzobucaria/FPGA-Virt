import socket
import struct
import os
import threading
import time
from typing import Dict, Tuple, Any
import logging

logger = logging.getLogger(__name__)

class UltraFastMMIOServer:
    """Server MMIO veloce con cache per skip verifiche ripetute"""
    
    def __init__(self, resource_manager, tenant_manager):
        self.resource_manager = resource_manager
        self.tenant_manager = tenant_manager
        self.socket_path = "/var/run/pynq/mmio_fast.sock"
        self.running = False
        
        # Cache token -> tenant_id per auth veloce
        self._auth_tokens: Dict[bytes, str] = {}
        
        # CACHE: (handle_str, tenant_id) -> mmio_object
        # Per skip verifiche su handle già validati
        self._mmio_cache: Dict[Tuple[str, str], Any] = {}
        self._cache_lock = threading.RLock()
        
    def start(self):
        """Avvia server ultra-veloce"""
        if os.path.exists(self.socket_path):
            os.unlink(self.socket_path)
        
        self.server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server_sock.bind(self.socket_path)
        self.server_sock.listen(10)
        os.chmod(self.socket_path, 0o666)
        
        self.running = True
        self.accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self.accept_thread.start()
        
        logger.info(f"Ultra-fast MMIO server started on {self.socket_path}")
    
    def _accept_loop(self):
        """Loop di accept connessioni"""
        while self.running:
            try:
                conn, _ = self.server_sock.accept()
                threading.Thread(
                    target=self._handle_client,
                    args=(conn,),
                    daemon=True
                ).start()
            except Exception as e:
                if self.running:
                    logger.error(f"Accept error: {e}")
    
    def _handle_client(self, conn):
        """Gestisce client con cache per performance ottimali"""
        tenant_id = None
        
        try:
            # 1. AUTH (una volta per connessione)
            auth_token = conn.recv(16)
            if auth_token not in self._auth_tokens:
                conn.send(b'\x00')
                conn.close()
                return
            
            tenant_id = self._auth_tokens[auth_token]
            conn.send(b'\x01')
            
            # 2. OPERATION LOOP
            while True:
                op_byte = conn.recv(1)
                if not op_byte:
                    break
                
                op = op_byte[0]
                
                if op == 0x01:  # WRITE
                    data = conn.recv(40)
                    if len(data) != 40:
                        break
                    
                    handle_str = data[:32].decode().strip()
                    offset, value = struct.unpack('!II', data[32:])
                    
                    # Check cache
                    cache_key = (handle_str, tenant_id)
                    
                    # Fast path - no lock needed for read
                    mmio_obj = self._mmio_cache.get(cache_key)
                    
                    if mmio_obj is not None:
                        # Cache hit! Skip all checks
                        try:
                            mmio_obj.write(offset, value)
                        except:
                            # Se fallisce, invalida cache
                            with self._cache_lock:
                                self._mmio_cache.pop(cache_key, None)
                    else:
                        # Cache miss - use resource manager
                        try:
                            self.resource_manager.mmio_write(
                                tenant_id, 
                                handle_str, 
                                offset, 
                                value
                            )
                            
                            # Add to cache on success
                            if handle_str in self.resource_manager._mmios:
                                mmio_obj = self.resource_manager._mmios[handle_str]
                                with self._cache_lock:
                                    self._mmio_cache[cache_key] = mmio_obj
                                    
                        except Exception as e:
                            # Solo log errori gravi
                            if logger.isEnabledFor(logging.DEBUG):
                                logger.debug(f"Write denied: {e}")
                    
                elif op == 0x02:  # READ
                    data = conn.recv(36)
                    if len(data) != 36:
                        break
                    
                    handle_str = data[:32].decode().strip()
                    offset = struct.unpack('!I', data[32:])[0]
                    
                    # Check cache
                    cache_key = (handle_str, tenant_id)
                    mmio_obj = self._mmio_cache.get(cache_key)
                    
                    if mmio_obj is not None:
                        # Cache hit!
                        try:
                            value = mmio_obj.read(offset)
                            conn.send(struct.pack('!I', value))
                        except:
                            # Fallback to resource manager
                            with self._cache_lock:
                                self._mmio_cache.pop(cache_key, None)
                            conn.send(b'\x00\x00\x00\x00')
                    else:
                        # Cache miss
                        try:
                            value = self.resource_manager.mmio_read(
                                tenant_id,
                                handle_str,
                                offset,
                                4
                            )
                            conn.send(struct.pack('!I', value))
                            
                            # Add to cache
                            if handle_str in self.resource_manager._mmios:
                                mmio_obj = self.resource_manager._mmios[handle_str]
                                with self._cache_lock:
                                    self._mmio_cache[cache_key] = mmio_obj
                                    
                        except Exception as e:
                            conn.send(b'\x00\x00\x00\x00')
                
                elif op == 0x06:  # WRITE_WITH_ACK
                    data = conn.recv(40)
                    if len(data) != 40:
                        break
                    
                    handle_str = data[:32].decode().strip()
                    offset, value = struct.unpack('!II', data[32:])
                    
                    # Check cache
                    cache_key = (handle_str, tenant_id)
                    mmio_obj = self._mmio_cache.get(cache_key)
                    
                    if mmio_obj is not None:
                        # Cache hit
                        try:
                            mmio_obj.write(offset, value)
                            conn.send(b'\x01')
                        except:
                            with self._cache_lock:
                                self._mmio_cache.pop(cache_key, None)
                            conn.send(b'\x00')
                    else:
                        # Cache miss
                        try:
                            self.resource_manager.mmio_write(
                                tenant_id,
                                handle_str,
                                offset,
                                value
                            )
                            conn.send(b'\x01')
                            
                            # Add to cache
                            if handle_str in self.resource_manager._mmios:
                                mmio_obj = self.resource_manager._mmios[handle_str]
                                with self._cache_lock:
                                    self._mmio_cache[cache_key] = mmio_obj
                                    
                        except Exception as e:
                            conn.send(b'\x00')
                        
                elif op == 0x10:  # BATCH_WRITE
                    count_data = conn.recv(2)
                    count = struct.unpack('!H', count_data)[0]
                    
                    success_count = 0
                    
                    for i in range(count):
                        op_data = conn.recv(40)
                        if len(op_data) != 40:
                            break
                            
                        handle_str = op_data[:32].decode().strip()
                        offset, value = struct.unpack('!II', op_data[32:])
                        
                        # Try cache first
                        cache_key = (handle_str, tenant_id)
                        mmio_obj = self._mmio_cache.get(cache_key)
                        
                        if mmio_obj is not None:
                            try:
                                mmio_obj.write(offset, value)
                                success_count += 1
                                continue
                            except:
                                with self._cache_lock:
                                    self._mmio_cache.pop(cache_key, None)
                        
                        # Fallback to resource manager
                        try:
                            self.resource_manager.mmio_write(
                                tenant_id,
                                handle_str,
                                offset,
                                value
                            )
                            success_count += 1
                            
                            # Try to cache
                            if handle_str in self.resource_manager._mmios:
                                mmio_obj = self.resource_manager._mmios[handle_str]
                                with self._cache_lock:
                                    self._mmio_cache[cache_key] = mmio_obj
                        except:
                            pass
                    
                    conn.send(struct.pack('!H', success_count))
        
        except Exception as e:
            logger.error(f"Client error: {e}")
        finally:
            conn.close()
    
    def register_token(self, token: str, tenant_id: str):
        """Pre-registra token per auth veloce"""
        token_bytes = token[:16].ljust(16, '\x00').encode()
        self._auth_tokens[token_bytes] = tenant_id
    
    def clear_cache(self):
        """Pulisce la cache (utile per test)"""
        with self._cache_lock:
            self._mmio_cache.clear()
    
    def stop(self):
        """Ferma server"""
        self.running = False
        if hasattr(self, 'server_sock'):
            self.server_sock.close()