# hypervisor/mock_resource_manager.py
import os
import threading
import uuid
import time
import random
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import logging

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
        """Genera IP cores fittizi per testing"""
        return {
            'axi_dma_0': {
                'phys_addr': 0xA0000000,
                'addr_range': 0x10000,
                'type': 'xilinx.com:ip:axi_dma:7.1',
                'parameters': {'data_width': 32}
            },
            'axi_gpio_0': {
                'phys_addr': 0xA0010000,
                'addr_range': 0x10000,
                'type': 'xilinx.com:ip:axi_gpio:2.0',
                'parameters': {'gpio_width': 32}
            },
            'custom_accel_0': {
                'phys_addr': 0xA0020000,
                'addr_range': 0x10000,
                'type': 'custom:hls:accelerator:1.0',
                'parameters': {}
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
        # Validazione già fatta nel ResourceManager, ma doppio controllo non fa male
        if offset < 0 or offset >= self.length:
            raise Exception(f"MMIO read offset {offset} out of range [0, {self.length})")
        
        if length not in [1, 2, 4, 8]:
            logger.warning(f"Non-standard read length {length}, defaulting to 4")
            length = 4
        
        if offset + length > self.length:
            raise Exception(f"MMIO read would exceed bounds")
        
        # Leggi valore byte per byte (più realistico)
        value = 0
        for i in range(length):
            byte_offset = offset + i
            byte_val = self._memory.get(byte_offset, 0)  # Default a 0 se non inizializzato
            value |= (byte_val << (i * 8))
        
        logger.debug(f"[MOCK] MMIO read: offset=0x{offset:04x}, length={length}, value=0x{value:08x}")
        return value
    
    def write(self, offset, value, length=4):
        """Simula scrittura MMIO con validazione migliorata"""
        # Validazione già fatta nel ResourceManager, ma doppio controllo non fa male
        if offset < 0 or offset >= self.length:
            raise Exception(f"MMIO write offset {offset} out of range [0, {self.length})")
        
        if length not in [1, 2, 4, 8]:
            logger.warning(f"Non-standard write length {length}, defaulting to 4")
            length = 4
            
        if offset + length > self.length:
            raise Exception(f"MMIO write would exceed bounds")
        
        # Scrivi valore byte per byte (più realistico)
        for i in range(length):
            byte_offset = offset + i
            byte_val = (value >> (i * 8)) & 0xFF
            self._memory[byte_offset] = byte_val
        
        logger.debug(f"[MOCK] MMIO write: offset=0x{offset:04x}, value=0x{value:08x}, length={length}")

class MockBuffer:
    def __init__(self, size):
        self.size = size
        self.physical_address = random.randint(0x80000000, 0x90000000)
        self.data = bytearray(size)
        logger.info(f"[MOCK] Allocated buffer: size={size}, phys_addr=0x{self.physical_address:08x}")
    
    def freebuffer(self):
        """Simula deallocazione buffer"""
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
                    ip_cores[name] = {
                        'name': name,
                        'type': str(ip.get('type', '')),
                        'base_address': base_addr,
                        'address_range': addr_range,
                        'parameters': {k: str(v) for k, v in ip.get('parameters', {}).items()}
                    }
            
            logger.info(f"[MOCK] Overlay loaded successfully: {handle}")
            return handle, ip_cores
    
    def create_mmio(self, tenant_id: str, overlay_id: str, ip_name: str,
                    base_address: int, length: int) -> str:
        """Simula creazione MMIO"""
        with self._lock:
            # Verifica che l'overlay appartenga al tenant
            if overlay_id not in self._resources:
                raise Exception("Overlay not found")
                
            resource = self._resources[overlay_id]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
            # Verifica permessi indirizzo
            if not self.tenant_manager.is_address_allowed(tenant_id, base_address, length):
                raise Exception("Address not allowed")
            
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
                    "overlay_id": overlay_id,
                    "ip_name": ip_name,
                    "base_address": base_address,
                    "length": length
                }
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].mmio_handles.add(handle)
            
            logger.info(f"[MOCK] MMIO created: {handle}")
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
            
            # Verifica che offset sia nel range (per scrittura singola)
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
    
    def allocate_buffer(self, tenant_id: str, size: int, buffer_type: int) -> Tuple[str, int]:
        """Simula allocazione buffer"""
        with self._lock:
            # Verifica limiti
            if not self.tenant_manager.can_allocate_buffer(tenant_id, size):
                raise Exception("Buffer allocation limit reached")
            
            # Alloca buffer mock
            buffer = MockBuffer(size)
            
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
                    "size": size,
                    "type": buffer_type,
                    "physical_address": buffer.physical_address
                }
            )
            
            # Aggiorna contatori tenant
            self.tenant_manager.resources[tenant_id].buffer_handles.add(handle)
            self.tenant_manager.resources[tenant_id].total_memory_bytes += size
            
            logger.info(f"[MOCK] Buffer allocated: {handle}")
            return handle, buffer.physical_address
    
    def create_dma(self, tenant_id: str, overlay_id: str, dma_name: str) -> Tuple[str, Dict]:
        """Simula creazione DMA"""
        with self._lock:
            # Verifica overlay
            if overlay_id not in self._resources:
                raise Exception("Overlay not found")
                
            resource = self._resources[overlay_id]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
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
                    "overlay_id": overlay_id,
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
            buffer.freebuffer()
            del self._buffers[handle]
        elif resource.resource_type == "dma":
            del self._dmas[handle]
            logger.info(f"[MOCK] Cleaned DMA: {handle}")
        
        # Rimuovi dai registri
        del self._resources[handle]