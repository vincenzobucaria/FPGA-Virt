# client/pynq_proxy/fast_mmio.py
import socket
import struct
import threading
import time
import os
import sys

# Import per creare handle iniziale
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from connection import Connection
import pynq_service_pb2 as pb2

class UltraFastMMIO:
    """Client MMIO ultra-veloce che bypassa gRPC"""
    
    _connection_pool = {}
    _pool_lock = threading.Lock()
    
    def __init__(self, base_addr: int, length: int = 4):
        self.base_addr = base_addr
        self.length = length
        self._handle_str = None
        self._handle_bytes = None
        self._conn = None
        
        self._get_connection()
        self._create_mmio_handle()
    
    def _get_connection(self):
        """Ottieni connessione dal pool"""
        socket_path = "/var/run/pynq/mmio_fast.sock"
        
        with self._pool_lock:
            if socket_path in self._connection_pool:
                self._conn = self._connection_pool[socket_path]
                try:
                    # Test se ancora viva
                    self._conn.send(b'')
                except:
                    del self._connection_pool[socket_path]
                    self._create_new_connection(socket_path)
            else:
                self._create_new_connection(socket_path)
    
    def _create_new_connection(self, socket_path):
        """Crea nuova connessione veloce"""
        self._conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._conn.connect(socket_path)
        
        # Auth con token semplice
        tenant_id = os.environ.get('TENANT_ID', 'tenant1')
        token = f"fast_{tenant_id}"
        token_bytes = token[:16].ljust(16, '\x00').encode()
        self._conn.send(token_bytes)
        
        response = self._conn.recv(1)
        if response != b'\x01':
            raise Exception("Fast protocol auth failed")
        
        self._connection_pool[socket_path] = self._conn
    
    def _create_mmio_handle(self):
        """Crea handle via gRPC normale"""
        conn = Connection()
        request = pb2.CreateMMIORequest(
            base_address=self.base_addr,
            length=self.length
        )
        
        response = conn.call_with_auth('CreateMMIO', request)
        self._handle_str = response.handle
        
        # Padda a 32 bytes per il protocollo
        self._handle_bytes = self._handle_str.ljust(32)[:32].encode()
    
    def write(self, offset: int, value: int):
        """Write veloce"""
        # Messaggio: op(1) + handle_str(32) + offset(4) + value(4) = 41 bytes
        msg = struct.pack('!B', 0x01) + self._handle_bytes + struct.pack('!II', offset, value)
        self._conn.send(msg)
    
    def read(self, offset: int, length: int = 4) -> int:
        """Read veloce"""
        # Messaggio: op(1) + handle_str(32) + offset(4) = 37 bytes  
        msg = struct.pack('!B', 0x02) + self._handle_bytes + struct.pack('!I', offset)
        self._conn.send(msg)
        
        data = self._conn.recv(4)
        return struct.unpack('!I', data)[0]
    
    def write_with_timing(self, offset: int, value: int) -> float:
        """Write con timing completo"""
        msg = struct.pack('!B', 0x06) + self._handle_bytes + struct.pack('!II', offset, value)
        
        start = time.perf_counter()
        self._conn.send(msg)
        ack = self._conn.recv(1)
        end = time.perf_counter()
        
        if ack != b'\x01':
            raise Exception("Write failed")
        
        return (end - start) * 1e6
    
    def benchmark_write_timing(self, offset: int = 0x28, iterations: int = 100):
        """Benchmark dettagliato dei tempi di scrittura"""
        import statistics
        
        print("Benchmarking Write Timing")
        print("="*60)
        
        # Test 1: Fire-and-forget
        print("\n1. Fire-and-forget write:")
        times_ff = []
        for i in range(iterations):
            start = time.perf_counter()
            self.write(offset, i)
            end = time.perf_counter()
            times_ff.append((end - start) * 1e6)
        
        print(f"   Mean:   {statistics.mean(times_ff):.2f} µs")
        print(f"   Median: {statistics.median(times_ff):.2f} µs")
        
        # Test 2: Write con ACK
        print("\n2. Write with ACK:")
        times_ack = []
        for i in range(iterations):
            time_us = self.write_with_timing(offset, i + 1000)
            times_ack.append(time_us)
        
        print(f"   Mean:   {statistics.mean(times_ack):.2f} µs")
        print(f"   Median: {statistics.median(times_ack):.2f} µs")
        
        # Breakdown
        print("\n3. Time breakdown:")
        network_time = statistics.mean(times_ff)
        total_time = statistics.mean(times_ack)
        hw_time = total_time - network_time
        
        print(f"   Network/Send time:  {network_time:.2f} µs")
        print(f"   Hardware time:      {hw_time:.2f} µs")
        print(f"   Total time:         {total_time:.2f} µs")

# Alias per compatibilità
FastMMIO = UltraFastMMIO