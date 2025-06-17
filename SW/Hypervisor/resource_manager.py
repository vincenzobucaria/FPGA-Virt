# hypervisor/resource_manager.py
import os
import threading
import uuid
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import time
# Import PYNQ reale (solo qui!)
import pynq

@dataclass
class ManagedResource:
    handle: str
    tenant_id: str
    resource_type: str
    created_at: float
    metadata: dict

class ResourceManager:
    def __init__(self, tenant_manager):
        self.tenant_manager = tenant_manager
        self._resources: Dict[str, ManagedResource] = {}
        self._overlays: Dict[str, pynq.Overlay] = {}
        self._mmios: Dict[str, pynq.MMIO] = {}
        self._buffers: Dict[str, pynq.Buffer] = {}
        self._dmas: Dict[str, pynq.lib.dma.DMA] = {}
        self._lock = threading.RLock()
        
    def _generate_handle(self, prefix: str) -> str:
        """Genera handle univoco"""
        return f"{prefix}_{uuid.uuid4().hex[:8]}"
    
    def load_overlay(self, tenant_id: str, bitfile_path: str) -> Tuple[str, Dict]:
        """Carica overlay per tenant"""
        with self._lock:
            # Verifica permessi
            if not self.tenant_manager.can_allocate_overlay(tenant_id):
                raise Exception("Overlay limit reached")
                
            if not self.tenant_manager.is_bitstream_allowed(tenant_id, os.path.basename(bitfile_path)):
                raise Exception("Bitstream not allowed")
            
            # Carica overlay
            full_path = os.path.join(self.tenant_manager.config[tenant_id].bitstream_dir, bitfile_path)
            if not os.path.exists(full_path):
                raise Exception(f"Bitstream not found: {bitfile_path}")
                
            overlay = pynq.Overlay(full_path)
            
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
                        'parameters': ip.get('parameters', {})
                    }
            
            return handle, ip_cores
    
    def create_mmio(self, tenant_id: str, overlay_id: str, ip_name: str,
                    base_address: int, length: int) -> str:
        """Crea MMIO per tenant"""
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
            
            # Crea MMIO
            mmio = pynq.MMIO(base_address, length)
            
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
            
            return handle
    
    def mmio_read(self, tenant_id: str, handle: str, offset: int, length: int) -> int:
        """Leggi da MMIO"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("MMIO handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("MMIO not owned by tenant")
            
            # Verifica che offset sia nel range
            mmio = self._mmios[handle]
            if offset + length > resource.metadata['length']:
                raise Exception("Offset out of range")
            
            # Leggi valore
            return mmio.read(offset, length)
    
    def mmio_write(self, tenant_id: str, handle: str, offset: int, value: int):
        """Scrivi su MMIO"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("MMIO handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("MMIO not owned by tenant")
            
            # Verifica che offset sia nel range
            mmio = self._mmios[handle]
            if offset >= resource.metadata['length']:
                raise Exception("Offset out of range")
            
            # Scrivi valore
            mmio.write(offset, value)
    
    def allocate_buffer(self, tenant_id: str, size: int, buffer_type: int) -> Tuple[str, int]:
        """Alloca buffer per tenant"""
        with self._lock:
            # Verifica limiti
            if not self.tenant_manager.can_allocate_buffer(tenant_id, size):
                raise Exception("Buffer allocation limit reached")
            
            # Alloca buffer
            from pynq import allocate
            buffer = allocate(shape=(size,), dtype='u1')
            
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
            
            return handle, buffer.physical_address
    
    def cleanup_tenant_resources(self, tenant_id: str):
        """Pulisce tutte le risorse di un tenant"""
        with self._lock:
            handles_to_remove = []
            
            for handle, resource in self._resources.items():
                if resource.tenant_id == tenant_id:
                    handles_to_remove.append(handle)
            
            for handle in handles_to_remove:
                self._cleanup_resource(handle)
    
    def _cleanup_resource(self, handle: str):
        """Pulisce una singola risorsa"""
        if handle not in self._resources:
            return
            
        resource = self._resources[handle]
        
        # Pulisci in base al tipo
        if resource.resource_type == "overlay":
            # PYNQ non ha un metodo unload esplicito
            del self._overlays[handle]
        elif resource.resource_type == "mmio":
            del self._mmios[handle]
        elif resource.resource_type == "buffer":
            buffer = self._buffers[handle]
            buffer.freebuffer()
            del self._buffers[handle]
        elif resource.resource_type == "dma":
            del self._dmas[handle]
        
        # Rimuovi dai registri
        del self._resources[handle]