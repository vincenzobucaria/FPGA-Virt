# hypervisor/mock_resource_manager.py
import os
import threading
import uuid
import time
import random
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import logging
import numpy as np
from multiprocessing import shared_memory
import mmap
logger = logging.getLogger(__name__)

@dataclass
class ManagedResource:
    handle: str
    tenant_id: str
    resource_type: str
    created_at: float
    metadata: dict

# Mock classes che simulano PYNQ
class MockOverlay:
    def __init__(self, bitfile_path):
        self.bitfile_path = bitfile_path
        self.ip_dict = self._generate_mock_ip_dict()
        logger.info(f"[MOCK] Loaded overlay: {bitfile_path}")
    
    def _generate_mock_ip_dict(self):
        """Genera IP cores fittizi per testing con register map"""
        return {
            'axi_dma_0': {
                'phys_addr': 0xA0000000,
                'addr_range': 0x10000,
                'type': 'xilinx.com:ip:axi_dma:7.1',
                'parameters': {'data_width': 32},
                'registers': {
                    'MM2S_DMACR': {'offset': 0x00, 'description': 'MM2S DMA Control'},
                    'MM2S_DMASR': {'offset': 0x04, 'description': 'MM2S DMA Status'},
                    'MM2S_SA': {'offset': 0x18, 'description': 'MM2S Source Address'},
                    'MM2S_LENGTH': {'offset': 0x28, 'description': 'MM2S Transfer Length'},
                    'S2MM_DMACR': {'offset': 0x30, 'description': 'S2MM DMA Control'},
                    'S2MM_DMASR': {'offset': 0x34, 'description': 'S2MM DMA Status'},
                    'S2MM_DA': {'offset': 0x48, 'description': 'S2MM Destination Address'},
                    'S2MM_LENGTH': {'offset': 0x58, 'description': 'S2MM Transfer Length'}
                }
            },
            'axi_gpio_0': {
                'phys_addr': 0xA0010000,
                'addr_range': 0x10000,
                'type': 'xilinx.com:ip:axi_gpio:2.0',
                'parameters': {'gpio_width': 32},
                'registers': {
                    'GPIO_DATA': {'offset': 0x00, 'description': 'GPIO Data Register'},
                    'GPIO_TRI': {'offset': 0x04, 'description': 'GPIO 3-state Control'},
                    'GPIO2_DATA': {'offset': 0x08, 'description': 'GPIO2 Data Register'},
                    'GPIO2_TRI': {'offset': 0x0C, 'description': 'GPIO2 3-state Control'},
                    'GIER': {'offset': 0x11C, 'description': 'Global Interrupt Enable'},
                    'IP_IER': {'offset': 0x128, 'description': 'IP Interrupt Enable'},
                    'IP_ISR': {'offset': 0x120, 'description': 'IP Interrupt Status'}
                }
            },
            'custom_accel_0': {
                'phys_addr': 0xA0020000,
                'addr_range': 0x10000,
                'type': 'custom:hls:accelerator:1.0',
                'parameters': {},
                'registers': {
                    # Registri di controllo standard HLS
                    'CTRL': {'offset': 0x00, 'description': 'Control signals'},
                    'GIE': {'offset': 0x04, 'description': 'Global Interrupt Enable'},
                    'IER': {'offset': 0x08, 'description': 'IP Interrupt Enable'},
                    'ISR': {'offset': 0x0C, 'description': 'IP Interrupt Status'},
                    # Registri per gli indirizzi dei buffer
                    'input': {'offset': 0x10, 'description': 'Input buffer address'},
                    'output': {'offset': 0x18, 'description': 'Output buffer address'},
                    # Registri per i parametri della convoluzione
                    'N': {'offset': 0x20, 'description': 'Batch size'},
                    'C_in': {'offset': 0x28, 'description': 'Input channels'},
                    'H_in': {'offset': 0x30, 'description': 'Input height'},
                    'W_in': {'offset': 0x38, 'description': 'Input width'},
                    'C_out': {'offset': 0x40, 'description': 'Output channels'},
                    'K_h': {'offset': 0x48, 'description': 'Kernel height'},
                    'K_w': {'offset': 0x50, 'description': 'Kernel width'},
                    'stride': {'offset': 0x58, 'description': 'Stride'},
                    'padding': {'offset': 0x60, 'description': 'Padding'}
                }
            }
        }   

class MockMMIO:
    def __init__(self, base_address, length):
        self.base_address = base_address
        self.length = length
        self._memory = {}  # Simula memoria
        logger.info(f"[MOCK] Created MMIO at 0x{base_address:08x}, length: {length}")
    
    def read(self, offset, length=4):
        """Simula lettura MMIO con validazione migliorata"""
        if offset < 0 or offset >= self.length:
            raise Exception(f"MMIO read offset {offset} out of range [0, {self.length})")
        
        if length not in [1, 2, 4, 8]:
            logger.warning(f"Non-standard read length {length}, defaulting to 4")
            length = 4
        
        if offset + length > self.length:
            raise Exception(f"MMIO read would exceed bounds")
        
        # Leggi valore byte per byte
        value = 0
        for i in range(length):
            byte_offset = offset + i
            byte_val = self._memory.get(byte_offset, 0)
            value |= (byte_val << (i * 8))
        
        logger.debug(f"[MOCK] MMIO read: offset=0x{offset:04x}, length={length}, value=0x{value:08x}")
        return value
    
    def write(self, offset, value, length=4):
        """Simula scrittura MMIO con validazione migliorata"""
        if offset < 0 or offset >= self.length:
            raise Exception(f"MMIO write offset {offset} out of range [0, {self.length})")
        
        if length not in [1, 2, 4, 8]:
            logger.warning(f"Non-standard write length {length}, defaulting to 4")
            length = 4
            
        if offset + length > self.length:
            raise Exception(f"MMIO write would exceed bounds")
        
        # Scrivi valore byte per byte
        for i in range(length):
            byte_offset = offset + i
            byte_val = (value >> (i * 8)) & 0xFF
            self._memory[byte_offset] = byte_val
        
        logger.debug(f"[MOCK] MMIO write: offset=0x{offset:04x}, value=0x{value:08x}, length={length}")

class MockBuffer:
    """Buffer migliorato con supporto numpy e shared memory"""
    
    def __init__(self, shape, dtype='uint8', use_shared_memory=True):
        # Parametri base
        self.shape = shape if isinstance(shape, tuple) else (shape,)
        self.dtype = np.dtype(dtype)
        self.size = np.prod(self.shape) * self.dtype.itemsize
        
        # Indirizzo fisico simulato
        self.physical_address = random.randint(0x80000000, 0x90000000)
        
        # Crea backing storage
        if use_shared_memory:
            # Usa shared memory per zero-copy con client locali
            self.shm = shared_memory.SharedMemory(create=True, size=self.size)
            self.shm_name = self.shm.name
            # Numpy array su shared memory
            self.data = np.ndarray(self.shape, dtype=self.dtype, buffer=self.shm.buf)
        else:
            # Buffer normale in memoria
            self.shm = None
            self.shm_name = None
            self.data = np.zeros(self.shape, dtype=self.dtype)
        
        logger.info(f"[MOCK] Allocated buffer: shape={shape}, dtype={dtype}, "
                   f"size={self.size} bytes, phys_addr=0x{self.physical_address:08x}, "
                   f"shm={self.shm_name}")
    
    def read(self, offset=0, length=None):
        """Leggi dati come bytes"""
        if length is None:
            return self.data.tobytes()[offset:]
        return self.data.tobytes()[offset:offset+length]
    
    def write(self, data_bytes, offset=0):
        """Scrivi bytes nel buffer"""
        # Converti bytes in array numpy
        temp_array = np.frombuffer(data_bytes, dtype=self.dtype)
        # Calcola quanti elementi copiare
        elements_to_copy = min(len(temp_array), self.data.size - offset // self.dtype.itemsize)
        # Copia nel buffer
        flat_view = self.data.flat
        start_idx = offset // self.dtype.itemsize
        flat_view[start_idx:start_idx + elements_to_copy] = temp_array[:elements_to_copy]
    
    def cleanup(self):
        """Pulisci risorse"""
        if self.shm:
            self.shm.close()
            self.shm.unlink()
        logger.info(f"[MOCK] Freed buffer at 0x{self.physical_address:08x}")


class MockDMA:
    def __init__(self, name):
        self.name = name
        self.sendchannel = self
        self.recvchannel = self
        logger.info(f"[MOCK] Created DMA: {name}")
    
    def transfer(self, buffer):
        """Simula trasferimento DMA"""
        logger.info(f"[MOCK] DMA transfer: {len(buffer)} bytes")
        return len(buffer)

class MockResourceManager:
    """Resource Manager che simula PYNQ per testing"""
    
    def __init__(self, tenant_manager):
        self.tenant_manager = tenant_manager
        self._resources: Dict[str, ManagedResource] = {}
        self._overlays: Dict[str, MockOverlay] = {}
        self._mmios: Dict[str, MockMMIO] = {}
        self._buffers: Dict[str, MockBuffer] = {}
        self._dmas: Dict[str, MockDMA] = {}
        self._lock = threading.RLock()
        
        logger.info("[MOCK] Initialized Mock Resource Manager")
        
    def _generate_handle(self, prefix: str) -> str:
        """Genera handle univoco"""
        return f"{prefix}_{uuid.uuid4().hex[:8]}"
    
    def load_overlay(self, tenant_id: str, bitfile_path: str) -> Tuple[str, Dict]:
        """Simula caricamento overlay"""
        with self._lock:
            # Verifica permessi
            if not self.tenant_manager.can_allocate_overlay(tenant_id):
                raise Exception("Overlay limit reached")
                
            if not self.tenant_manager.is_bitstream_allowed(tenant_id, os.path.basename(bitfile_path)):
                raise Exception("Bitstream not allowed")
            
            # Simula caricamento
            logger.info(f"[MOCK] Loading overlay {bitfile_path} for tenant {tenant_id}")
            time.sleep(0.1)  # Simula delay caricamento
            
            overlay = MockOverlay(bitfile_path)
            
            # Genera handle
            handle = self._generate_handle("overlay")
            
            # Salva riferimenti
            self._overlays[handle] = overlay
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="overlay",
                created_at=time.time(),
                metadata={"bitfile": bitfile_path}
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].overlays.add(handle)
            
            # Prepara risposta con IP cores
            ip_cores = {}
            for name, ip in overlay.ip_dict.items():
                
                # Filtra solo IP accessibili al tenant
                base_addr = ip.get('phys_addr', 0)
                addr_range = ip.get('addr_range', 0)
                if self.tenant_manager.is_address_allowed(tenant_id, base_addr, addr_range):
                    print("ALLOWED: ", name, ip)
                    ip_cores[name] = {
                        'name': name,
                        'type': str(ip.get('type', '')),
                        'base_address': base_addr,
                        'address_range': addr_range,
                        'parameters': {k: str(v) for k, v in ip.get('parameters', {}).items()},
                        'registers': ip.get('registers', {})  # <-- AGGIUNGI QUESTA RIGA
                    }                
            
            logger.info(f"[MOCK] Overlay loaded successfully: {handle}")
            return handle, ip_cores
    
    def create_mmio(self, tenant_id: str, base_address: int, length: int) -> str:
        """Crea MMIO - SEMPLIFICATO senza overlay_id"""
        with self._lock:
            # UNICA verifica necessaria: il tenant può accedere a questo indirizzo?
            if not self.tenant_manager.is_address_allowed(tenant_id, base_address, length):
                raise Exception(f"Tenant {tenant_id} not allowed to access address 0x{base_address:08x}")
            
            # Crea MMIO mock
            mmio = MockMMIO(base_address, length)
            
            # Genera handle
            handle = self._generate_handle("mmio")
            
            # Salva riferimenti
            self._mmios[handle] = mmio
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="mmio",
                created_at=time.time(),
                metadata={
                    "base_address": base_address,
                    "length": length
                }
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].mmio_handles.add(handle)
            
            logger.info(f"[MOCK] MMIO created: {handle} for tenant {tenant_id} at 0x{base_address:08x}")
            return handle
    
    def mmio_read(self, tenant_id: str, handle: str, offset: int, length: int) -> int:
        """Simula lettura MMIO con controlli di sicurezza completi"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("MMIO handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("MMIO not owned by tenant")
            
            # Ottieni info dal metadata
            base_address = resource.metadata['base_address']
            mmio_length = resource.metadata['length']
            
            # Verifica che offset non sia negativo
            if offset < 0:
                raise Exception(f"Negative offset not allowed: {offset}")
            
            # Verifica che la lettura non vada oltre i limiti del MMIO
            if offset + length > mmio_length:
                raise Exception(f"Read out of bounds: offset {offset} + length {length} > MMIO size {mmio_length}")
            
            # Verifica che il tenant possa ancora accedere all'indirizzo effettivo
            actual_address = base_address + offset
            if not self.tenant_manager.is_address_allowed(tenant_id, actual_address, length):
                raise Exception(f"Tenant {tenant_id} no longer allowed to access address 0x{actual_address:08x}")
            
            # Leggi valore simulato
            mmio = self._mmios[handle]
            value = mmio.read(offset, length)
            
            logger.debug(f"MMIO read by {tenant_id}: handle={handle}, addr=0x{actual_address:08x}, value=0x{value:08x}")
            return value
    
    def mmio_write(self, tenant_id: str, handle: str, offset: int, value: int):
        """Simula scrittura MMIO con controlli di sicurezza completi"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("MMIO handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("MMIO not owned by tenant")
            
            # Ottieni info dal metadata
            base_address = resource.metadata['base_address']
            mmio_length = resource.metadata['length']
            
            # Verifica che offset non sia negativo
            if offset < 0:
                raise Exception(f"Negative offset not allowed: {offset}")
            
            # Verifica che offset sia nel range
            if offset >= mmio_length:
                raise Exception(f"Write offset out of bounds: offset {offset} >= MMIO size {mmio_length}")
            
            # Assumiamo scritture a 32-bit (4 bytes) per default
            write_length = 4
            if offset + write_length > mmio_length:
                raise Exception(f"Write would exceed MMIO bounds: offset {offset} + {write_length} > MMIO size {mmio_length}")
            
            # Verifica che il tenant possa ancora accedere all'indirizzo effettivo
            actual_address = base_address + offset
            if not self.tenant_manager.is_address_allowed(tenant_id, actual_address, write_length):
                raise Exception(f"Tenant {tenant_id} no longer allowed to access address 0x{actual_address:08x}")
            
            # Verifica che il valore sia nel range di 32-bit
            if value < 0 or value > 0xFFFFFFFF:
                raise Exception(f"Value out of 32-bit range: {value}")
            
            # Scrivi valore
            mmio = self._mmios[handle]
            mmio.write(offset, value)
            
            logger.debug(f"MMIO write by {tenant_id}: handle={handle}, addr=0x{actual_address:08x}, value=0x{value:08x}")
    
    def allocate_buffer(self, tenant_id: str, shape, dtype='uint8') -> Dict:
        """Alloca buffer con supporto numpy e shared memory"""
        with self._lock:
            # Calcola size
            np_shape = tuple(shape) if isinstance(shape, (list, tuple)) else (shape,)
            size = np.prod(np_shape) * np.dtype(dtype).itemsize
            
            # Verifica limiti
            if not self.tenant_manager.can_allocate_buffer(tenant_id, size):
                raise Exception("Buffer allocation limit reached")
            
            # Decidi se usare shared memory
            use_shm = size > 1024  # Usa shm per buffer > 1KB
            
            # Alloca buffer mock
            buffer = MockBuffer(np_shape, dtype, use_shared_memory=use_shm)
            
            # Genera handle
            handle = self._generate_handle("buffer")
            
            # Salva riferimenti
            self._buffers[handle] = buffer
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="buffer",
                created_at=time.time(),
                metadata={
                    "shape": np_shape,
                    "dtype": str(dtype),
                    "size": size,
                    "physical_address": buffer.physical_address,
                    "shm_name": buffer.shm_name
                }
            )
            
            # Aggiorna contatori tenant
            self.tenant_manager.resources[tenant_id].buffer_handles.add(handle)
            self.tenant_manager.resources[tenant_id].total_memory_bytes += size
            
            logger.info(f"[MOCK] Buffer allocated: {handle}")
            
            # Ritorna info complete per il client
            return {
                'handle': handle,
                'physical_address': buffer.physical_address,
                'total_size': size,
                'shm_name': buffer.shm_name,
                'shape': np_shape,
                'dtype': str(dtype)
            }
    
    def read_buffer(self, tenant_id: str, handle: str, offset: int, length: int) -> bytes:
        """Leggi dati da buffer"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Leggi dati
            buffer = self._buffers[handle]
            return buffer.read(offset, length)
    
    def write_buffer(self, tenant_id: str, handle: str, data: bytes, offset: int):
        """Scrivi dati in buffer"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Scrivi dati
            buffer = self._buffers[handle]
            buffer.write(data, offset)

    
    def create_dma(self, tenant_id: str, dma_name: str) -> Tuple[str, Dict]:
        """Crea DMA - SEMPLIFICATO senza overlay_id"""
        with self._lock:
            # Verifica che il tenant abbia almeno un overlay caricato
            tenant_overlays = [
                res for _, res in self._resources.items()
                if res.tenant_id == tenant_id and res.resource_type == "overlay"
            ]
            
            if not tenant_overlays:
                raise Exception("No overlay loaded for tenant")
            
            # Crea DMA mock
            dma = MockDMA(dma_name)
            
            # Genera handle
            handle = self._generate_handle("dma")
            
            # Salva riferimenti
            self._dmas[handle] = dma
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="dma",
                created_at=time.time(),
                metadata={
                    "dma_name": dma_name
                }
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].dma_handles.add(handle)
            
            info = {
                'has_send_channel': True,
                'has_recv_channel': True,
                'max_transfer_size': 16 * 1024 * 1024  # 16MB
            }
            
            logger.info(f"[MOCK] DMA created: {handle}")
            return handle, info
        
        
    def get_tenant_resources_summary(self, tenant_id: str) -> dict:
        """Ottieni riepilogo risorse allocate per un tenant"""
        with self._lock:
            resources = {
                'overlays': 0,
                'mmios': 0,
                'buffers': 0,
                'dmas': 0,
                'total_memory': 0
            }
            
            for handle, resource in self._resources.items():
                if resource.tenant_id == tenant_id:
                    if resource.resource_type == "overlay":
                        resources['overlays'] += 1
                    elif resource.resource_type == "mmio":
                        resources['mmios'] += 1
                    elif resource.resource_type == "buffer":
                        resources['buffers'] += 1
                        resources['total_memory'] += resource.metadata.get('size', 0)
                    elif resource.resource_type == "dma":
                        resources['dmas'] += 1
            
            return resources



    def cleanup_tenant_resources(self, tenant_id: str):
        """Pulisce tutte le risorse di un tenant"""
        with self._lock:
            handles_to_remove = []
            
            for handle, resource in self._resources.items():
                if resource.tenant_id == tenant_id:
                    handles_to_remove.append(handle)
            
            for handle in handles_to_remove:
                self._cleanup_resource(handle)
                
            logger.info(f"[MOCK] Cleaned up all resources for tenant {tenant_id}")
    
    def _cleanup_resource(self, handle: str):
        """Pulisce una singola risorsa"""
        if handle not in self._resources:
            return
            
        resource = self._resources[handle]
        
        # Pulisci in base al tipo
        if resource.resource_type == "overlay":
            del self._overlays[handle]
            logger.info(f"[MOCK] Cleaned overlay: {handle}")
        elif resource.resource_type == "mmio":
            del self._mmios[handle]
            logger.info(f"[MOCK] Cleaned MMIO: {handle}")
        elif resource.resource_type == "buffer":
            buffer = self._buffers[handle]
            buffer.cleanup()  # <-- FIX: era freebuffer()
            del self._buffers[handle]
            logger.info(f"[MOCK] Cleaned buffer: {handle}")
        elif resource.resource_type == "dma":
            del self._dmas[handle]
            logger.info(f"[MOCK] Cleaned DMA: {handle}")
        
        # Rimuovi dai registri
        del self._resources[handle]