# hypervisor/pynq_resource_manager.py - CODICE COMPLETO con single thread
import os
import threading
import uuid
import time
from typing import Dict, Optional, Tuple, List, Set
from dataclasses import dataclass
import logging
import numpy as np

# Import nostri moduli
from pr_zone_manager import PRZoneManager
from hardware_thread_manager import get_hardware_thread_manager

logger = logging.getLogger(__name__)

@dataclass
class ManagedResource:
    handle: str
    tenant_id: str
    resource_type: str
    created_at: float
    metadata: dict
    pynq_object: any = None

class PYNQResourceManager:
    """Resource Manager che usa un singolo thread per tutte le operazioni hardware"""
    
    def __init__(self, tenant_manager, config_manager=None):
        self.tenant_manager = tenant_manager
        self.config_manager = config_manager
        self._resources: Dict[str, ManagedResource] = {}
        self._lock = threading.RLock()
        
        # Directory bitstream
        self.bitstream_dir = '/home/xilinx/bitstreams'
        if config_manager:
            self.bitstream_dir = config_manager.bitstream_dir
        
        # Ottieni il manager del thread hardware
        self.hw_manager = get_hardware_thread_manager()
        
        # Avvia il thread hardware se non è già attivo
        self.hw_manager.start()
        
        # Inizializza PR Zone Manager (locale, non hardware)
        num_pr_zones = 2
        if config_manager and hasattr(config_manager, 'num_pr_zones'):
            num_pr_zones = config_manager.num_pr_zones
            
        self.pr_zone_manager = PRZoneManager(num_pr_zones)
        
        # Mappa degli indirizzi per PR zone
        self.pr_zone_addresses = {}
        self._initialize_pr_zone_addresses()
        
        logger.info("[PYNQ] Resource Manager initialized with single hardware thread")
    
    def _initialize_pr_zone_addresses(self):
        """Inizializza la mappa degli indirizzi per PR zone"""
        if not self.config_manager:
            # Default addresses
            self.pr_zone_addresses = {
                0: [(0xA0000000, 0x10000), (0xA0010000, 0x10000)],
                1: [(0xA0100000, 0x10000), (0xA0110000, 0x10000)]
            }
            return
        
        # Leggi dalla configurazione
        pr_zones_config = getattr(self.config_manager, 'pr_zones', [])
        for zone_config in pr_zones_config:
            zone_id = zone_config.zone_id if hasattr(zone_config, 'zone_id') else zone_config.get('zone_id')
            address_ranges = zone_config.address_ranges if hasattr(zone_config, 'address_ranges') else zone_config.get('address_ranges', [])
            
            if zone_id is not None:
                self.pr_zone_addresses[zone_id] = [tuple(r) for r in address_ranges]
                logger.info(f"[PYNQ] Zone {zone_id} addresses: {self.pr_zone_addresses[zone_id]}")
    
    def _generate_handle(self, prefix: str) -> str:
        """Genera handle univoco"""
        return f"{prefix}_{uuid.uuid4().hex[:8]}"
    
    def load_overlay(self, tenant_id: str, bitfile_path: str) -> Tuple[str, Dict]:
        """
        Carica overlay usando il thread hardware dedicato.
        """
        with self._lock:
            # Verifica permessi base
            if not self.tenant_manager.can_allocate_overlay(tenant_id):
                raise Exception("Overlay limit reached")
            
            # Ottieni config del tenant
            tenant_config = self.tenant_manager.config.get(tenant_id)
            if not tenant_config:
                raise Exception(f"Tenant {tenant_id} not found")
            
            allowed_bitstreams = tenant_config.allowed_bitstreams or set()
            
            # Usa PR Zone Manager per trovare la zona migliore
            result = self.pr_zone_manager.find_best_zone_for_bitstream(
                bitfile_path,
                tenant_id,
                self.bitstream_dir,
                allowed_bitstreams
            )
            
            if not result:
                raise Exception(f"No available PR zone for bitstream {bitfile_path}")
            
            zone_id, actual_bitstream_path = result
            
            # Verifica permessi zona
            if hasattr(tenant_config, 'allowed_pr_zones'):
                if zone_id not in tenant_config.allowed_pr_zones:
                    raise Exception(f"Tenant {tenant_id} not allowed to use PR zone {zone_id}")
            
            logger.info(f"[PYNQ] Loading partial bitstream {actual_bitstream_path} "
                       f"in PR zone {zone_id} for tenant {tenant_id}")
            
            # IMPORTANTE: Esegui la riconfigurazione nel thread hardware dedicato
            try:
                success = self.hw_manager.load_pr_bitstream(
                    tenant_id, zone_id, actual_bitstream_path
                )
            except Exception as e:
                logger.error(f"[PYNQ] Hardware operation failed: {e}")
                raise Exception(f"Failed to load bitstream: {e}")
            
            if not success:
                raise Exception(f"Failed to reconfigure PR zone {zone_id}")
            
            # Genera handle
            handle = self._generate_handle("overlay")
            
            # Alloca la zona PR
            if not self.pr_zone_manager.allocate_zone(
                tenant_id, zone_id, actual_bitstream_path, handle
            ):
                raise Exception(f"Failed to allocate PR zone {zone_id}")
            
            # Salva riferimenti
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="overlay",
                created_at=time.time(),
                metadata={
                    "bitfile": actual_bitstream_path,
                    "pr_zone": zone_id,
                    "requested_bitfile": bitfile_path,
                    "partial": True
                },
                pynq_object=None
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].overlays.add(handle)
            
            # Per bitstream parziali, gli IP cores sono definiti dalla zona PR
            ip_cores = self._get_pr_zone_ip_cores(zone_id)
            
            logger.info(f"[PYNQ] Partial bitstream loaded successfully: {handle} "
                       f"in PR zone {zone_id}")
            
            return handle, ip_cores
    
    def _get_pr_zone_ip_cores(self, zone_id: int) -> Dict:
        """Ottieni gli IP cores per una specifica PR zone"""
        ip_cores = {}
        
        # Ottieni indirizzi per questa zona
        zone_addresses = self.pr_zone_addresses.get(zone_id, [])
        
        for i, (base_addr, size) in enumerate(zone_addresses):
            ip_name = f"pr{zone_id}_ip{i}"
            ip_cores[ip_name] = {
                'name': ip_name,
                'type': 'custom_ip',
                'base_address': base_addr,
                'address_range': size,
                'parameters': {},
                'registers': {
                    'CONTROL': {'offset': 0x00, 'description': 'Control register'},
                    'STATUS': {'offset': 0x04, 'description': 'Status register'},
                    'DATA': {'offset': 0x08, 'description': 'Data register'},
                    'CONFIG': {'offset': 0x0C, 'description': 'Configuration register'}
                }
            }
        
        return ip_cores
    
    def get_ip_object(self, tenant_id: str, overlay_handle: str, ip_name: str):
        """Ottieni l'oggetto IP - non disponibile per bitstream parziali"""
        with self._lock:
            if overlay_handle not in self._resources:
                raise Exception("Overlay handle not found")
            
            resource = self._resources[overlay_handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
            # Per bitstream parziali, non c'è un vero oggetto IP PYNQ
            if resource.metadata.get('partial', False):
                logger.warning(f"IP objects not available for partial bitstreams")
                return None
            
            raise NotImplementedError("Full overlay IP objects not implemented")
    
    def create_mmio(self, tenant_id: str, base_address: int, length: int) -> str:
        """Crea MMIO usando il thread hardware dedicato"""
        with self._lock:
            # Verifica permessi
            tenant_zones = self.pr_zone_manager.get_tenant_zones(tenant_id)
            
            address_allowed = False
            allowed_zone = None
            
            for zone_id in tenant_zones:
                zone_addresses = self.pr_zone_addresses.get(zone_id, [])
                for allowed_base, allowed_size in zone_addresses:
                    if (base_address >= allowed_base and 
                        base_address + length <= allowed_base + allowed_size):
                        address_allowed = True
                        allowed_zone = zone_id
                        break
                if address_allowed:
                    break
            
            if not address_allowed:
                if not tenant_zones:
                    raise Exception(f"Tenant {tenant_id} has no PR zones allocated")
                raise Exception(f"Address 0x{base_address:08x} not allowed")
            
            logger.info(f"[PYNQ] Creating MMIO at 0x{base_address:08x} for zone {allowed_zone}")
            
            # IMPORTANTE: Crea MMIO nel thread hardware
            try:
                hw_handle = self.hw_manager.create_mmio(tenant_id, base_address, length)
            except Exception as e:
                logger.error(f"[PYNQ] Failed to create MMIO: {e}")
                raise Exception(f"Failed to create MMIO: {e}")
            
            # Genera handle locale
            handle = self._generate_handle("mmio")
            
            # Salva riferimenti
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="mmio",
                created_at=time.time(),
                metadata={
                    "base_address": base_address,
                    "length": length,
                    "pr_zone": allowed_zone,
                    "hw_handle": hw_handle  # Handle nel thread hardware
                },
                pynq_object=None
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].mmio_handles.add(handle)
            
            logger.info(f"[PYNQ] MMIO created: {handle} at 0x{base_address:08x}")
            return handle
    
    def mmio_read(self, tenant_id: str, handle: str, offset: int, length: int = 4) -> int:
        """Legge da MMIO usando il thread hardware"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("MMIO handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("MMIO not owned by tenant")
            
            # Verifica limiti
            mmio_length = resource.metadata['length']
            if offset < 0 or offset + length > mmio_length:
                raise Exception("Read out of bounds")
            
            # Ottieni handle hardware
            hw_handle = resource.metadata.get('hw_handle')
            if not hw_handle:
                raise Exception("Hardware handle not found")
            
            # IMPORTANTE: Leggi nel thread hardware
            try:
                value = self.hw_manager.mmio_read(tenant_id, hw_handle, offset, length)
                logger.debug(f"[PYNQ] MMIO read: offset=0x{offset:04x}, value=0x{value:08x}")
                return value
            except Exception as e:
                logger.error(f"[PYNQ] MMIO read failed: {e}")
                raise
    
    def mmio_write(self, tenant_id: str, handle: str, offset: int, value: int):
        """Scrive su MMIO usando il thread hardware"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("MMIO handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("MMIO not owned by tenant")
            
            # Verifica limiti  
            mmio_length = resource.metadata['length']
            if offset < 0 or offset + 4 > mmio_length:
                raise Exception("Write out of bounds")
            
            if value < 0 or value > 0xFFFFFFFF:
                raise Exception(f"Value out of range")
            
            # Ottieni handle hardware
            hw_handle = resource.metadata.get('hw_handle')
            if not hw_handle:
                raise Exception("Hardware handle not found")
            
            # IMPORTANTE: Scrivi nel thread hardware
            try:
                self.hw_manager.mmio_write(tenant_id, hw_handle, offset, value)
                logger.debug(f"[PYNQ] MMIO write: offset=0x{offset:04x}, value=0x{value:08x}")
            except Exception as e:
                logger.error(f"[PYNQ] MMIO write failed: {e}")
                raise
    
    def allocate_buffer(self, tenant_id: str, shape, dtype='uint8') -> Dict:
        """Alloca buffer usando il thread hardware"""
        with self._lock:
            # Calcola size
            np_shape = tuple(shape) if isinstance(shape, (list, tuple)) else (shape,)
            np_dtype = np.dtype(dtype)
            size = np.prod(np_shape) * np_dtype.itemsize
            
            # Verifica limiti tenant
            if not self.tenant_manager.can_allocate_buffer(tenant_id, size):
                raise Exception("Buffer allocation limit reached")
            
            # IMPORTANTE: Alloca nel thread hardware
            try:
                hw_handle, physical_address = self.hw_manager.allocate_buffer(
                    tenant_id, shape, dtype
                )
            except Exception as e:
                logger.error(f"[PYNQ] Failed to allocate buffer: {e}")
                raise Exception(f"Failed to allocate buffer: {e}")
            
            # Genera handle locale
            handle = self._generate_handle("buffer")
            
            # Salva riferimenti
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="buffer",
                created_at=time.time(),
                metadata={
                    "shape": np_shape,
                    "dtype": str(np_dtype),
                    "size": size,
                    "physical_address": physical_address,
                    "hw_handle": hw_handle
                },
                pynq_object=None
            )
            
            # Aggiorna contatori tenant
            self.tenant_manager.resources[tenant_id].buffer_handles.add(handle)
            self.tenant_manager.resources[tenant_id].total_memory_bytes += size
            
            logger.info(f"[PYNQ] Buffer allocated: {handle}, phys_addr=0x{physical_address:08x}, size={size} bytes")
            
            return {
                'handle': handle,
                'physical_address': physical_address,
                'total_size': size,
                'shm_name': None,
                'shape': np_shape,
                'dtype': str(np_dtype)
            }
    
    def read_buffer(self, tenant_id: str, handle: str, offset: int, length: int) -> bytes:
        """Leggi dati da buffer usando il thread hardware"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Verifica limiti
            buffer_size = resource.metadata['size']
            if offset < 0 or offset >= buffer_size:
                raise Exception(f"Offset {offset} out of bounds [0, {buffer_size})")
            
            if offset + length > buffer_size:
                raise Exception(f"Read would exceed buffer bounds")
            
            # Ottieni handle hardware
            hw_handle = resource.metadata.get('hw_handle')
            if not hw_handle:
                raise Exception("Hardware handle not found")
            
            # IMPORTANTE: Leggi nel thread hardware
            try:
                data = self.hw_manager.read_buffer(tenant_id, hw_handle, offset, length)
                logger.debug(f"[PYNQ] Buffer read: handle={handle}, offset={offset}, length={length}")
                return data
            except Exception as e:
                logger.error(f"[PYNQ] Buffer read failed: {e}")
                raise
    
    def write_buffer(self, tenant_id: str, handle: str, data: bytes, offset: int):
        """Scrivi dati in buffer usando il thread hardware"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Verifica limiti
            buffer_size = resource.metadata['size']
            data_length = len(data)
            
            if offset < 0 or offset >= buffer_size:
                raise Exception(f"Offset {offset} out of bounds [0, {buffer_size})")
            
            if offset + data_length > buffer_size:
                raise Exception(f"Write would exceed buffer bounds")
            
            # Ottieni handle hardware
            hw_handle = resource.metadata.get('hw_handle')
            if not hw_handle:
                raise Exception("Hardware handle not found")
            
            # IMPORTANTE: Scrivi nel thread hardware
            try:
                self.hw_manager.write_buffer(tenant_id, hw_handle, data, offset)
                logger.debug(f"[PYNQ] Buffer write: handle={handle}, offset={offset}, length={data_length}")
            except Exception as e:
                logger.error(f"[PYNQ] Buffer write failed: {e}")
                raise
    
    def free_buffer(self, tenant_id: str, handle: str):
        """Libera un buffer"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Ottieni info
            size = resource.metadata['size']
            hw_handle = resource.metadata.get('hw_handle')
            
            if hw_handle:
                try:
                    # Libera nel thread hardware
                    self.hw_manager.free_buffer(tenant_id, hw_handle)
                except Exception as e:
                    logger.error(f"[PYNQ] Error freeing buffer: {e}")
            
            # Aggiorna contatori tenant
            self.tenant_manager.resources[tenant_id].buffer_handles.discard(handle)
            self.tenant_manager.resources[tenant_id].total_memory_bytes -= size
            
            # Rimuovi riferimenti
            del self._resources[handle]
            
            logger.info(f"[PYNQ] Buffer freed: handle={handle}, size={size} bytes")
    
    def create_dma(self, tenant_id: str, dma_name: str) -> Tuple[str, Dict]:
        """Crea DMA handle"""
        with self._lock:
            # Verifica che il tenant abbia almeno una PR zone allocata
            tenant_zones = self.pr_zone_manager.get_tenant_zones(tenant_id)
            if not tenant_zones:
                raise Exception(f"Tenant {tenant_id} has no PR zones allocated")
            
            # Per ora assumiamo che il DMA sia nella prima zona del tenant
            zone_id = list(tenant_zones)[0]
            
            logger.info(f"[PYNQ] Creating DMA {dma_name} for tenant {tenant_id} in zone {zone_id}")
            
            # Genera handle
            handle = self._generate_handle("dma")
            
            # Salva riferimenti
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="dma",
                created_at=time.time(),
                metadata={
                    "dma_name": dma_name,
                    "pr_zone": zone_id
                },
                pynq_object=None
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].dma_handles.add(handle)
            
            # Info DMA (esempio)
            dma_info = {
                'has_send_channel': True,
                'has_recv_channel': True,
                'max_transfer_size': 67108864  # 64MB
            }
            
            return handle, dma_info
    
    def unload_overlay(self, tenant_id: str, handle: str):
        """Scarica overlay e libera la PR zone"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Overlay handle not found")
            
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
            # Ottieni zona PR dal metadata
            zone_id = resource.metadata.get('pr_zone')
            
            # Rilascia la PR zone
            released_zone = self.pr_zone_manager.release_zone_by_handle(handle)
            if released_zone is not None:
                logger.info(f"[PYNQ] Released PR zone {released_zone} for overlay {handle}")
            
            # Rimuovi da registri
            del self._resources[handle]
            self.tenant_manager.resources[tenant_id].overlays.discard(handle)
            
            logger.info(f"[PYNQ] Unloaded overlay {handle}")
    
    def get_tenant_resources_summary(self, tenant_id: str) -> dict:
        """Ottieni riepilogo risorse allocate per un tenant"""
        with self._lock:
            resources = {
                'overlays': 0,
                'mmios': 0,
                'buffers': 0,
                'dmas': 0,
                'total_memory': 0,
                'pr_zones': []
            }
            
            # Conta risorse
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
            
            # Aggiungi info PR zones
            resources['pr_zones'] = list(self.pr_zone_manager.get_tenant_zones(tenant_id))
            
            return resources
    
    def get_pr_zone_status(self) -> Dict:
        """Ottieni stato delle PR zones"""
        base_info = self.pr_zone_manager.get_allocation_info()
        
        # Aggiungi indirizzi per ogni zona
        for zone_id in range(self.pr_zone_manager.num_pr_zones):
            zone_key = f'PR_{zone_id}'
            if zone_key not in base_info['allocations']:
                base_info['allocations'][zone_key] = {}
            
            base_info['allocations'][zone_key]['addresses'] = \
                self.pr_zone_addresses.get(zone_id, [])
        
        return base_info
    
    def cleanup_tenant_resources(self, tenant_id: str):
        """Pulisce tutte le risorse di un tenant"""
        with self._lock:
            # Prima rilascia tutte le PR zones del tenant
            released_zones = self.pr_zone_manager.release_all_tenant_zones(tenant_id)
            if released_zones:
                logger.info(f"[PYNQ] Released PR zones {released_zones} for tenant {tenant_id}")
            
            # Trova tutte le risorse del tenant
            handles_to_remove = []
            for handle, resource in self._resources.items():
                if resource.tenant_id == tenant_id:
                    handles_to_remove.append(handle)
            
            # Pulisci ogni risorsa
            for handle in handles_to_remove:
                self._cleanup_resource(handle)
            
            logger.info(f"[PYNQ] Cleaned up all resources for tenant {tenant_id}")
    
    def _cleanup_resource(self, handle: str):
        """Pulisce una singola risorsa"""
        if handle not in self._resources:
            return
            
        resource = self._resources[handle]
        
        try:
            if resource.resource_type == "overlay":
                # Overlay/bitstream parziali
                logger.info(f"[PYNQ] Cleaned overlay: {handle}")
                
            elif resource.resource_type == "mmio":
                # Distruggi MMIO nel thread hardware
                hw_handle = resource.metadata.get('hw_handle')
                if hw_handle:
                    try:
                        self.hw_manager.destroy_mmio(resource.tenant_id, hw_handle)
                    except:
                        pass
                logger.info(f"[PYNQ] Cleaned MMIO: {handle}")
                
            elif resource.resource_type == "buffer":
                # Libera buffer nel thread hardware
                hw_handle = resource.metadata.get('hw_handle')
                if hw_handle:
                    try:
                        self.hw_manager.free_buffer(resource.tenant_id, hw_handle)
                    except:
                        pass
                
                # Aggiorna contatori
                size = resource.metadata.get('size', 0)
                self.tenant_manager.resources[resource.tenant_id].buffer_handles.discard(handle)
                self.tenant_manager.resources[resource.tenant_id].total_memory_bytes -= size
                
                logger.info(f"[PYNQ] Cleaned buffer: {handle}")
                
            elif resource.resource_type == "dma":
                logger.info(f"[PYNQ] Cleaned DMA: {handle}")
            
            # Rimuovi dai registri del tenant manager
            if resource.resource_type == "overlay":
                self.tenant_manager.resources[resource.tenant_id].overlays.discard(handle)
            elif resource.resource_type == "mmio":
                self.tenant_manager.resources[resource.tenant_id].mmio_handles.discard(handle)
            elif resource.resource_type == "dma":
                self.tenant_manager.resources[resource.tenant_id].dma_handles.discard(handle)
            
        except Exception as e:
            logger.error(f"[PYNQ] Error cleaning up {resource.resource_type} {handle}: {e}")
        
        # Rimuovi dai registri
        del self._resources[handle]