import os
import threading
import uuid
import time
import asyncio
from typing import Dict, Optional, Tuple, List, Set
from dataclasses import dataclass
import logging
import numpy as np
import concurrent.futures

# Import PYNQ reale
from pynq import Overlay as PYNQOverlay
from pynq import allocate as pynq_allocate
from pynq.mmio import MMIO as PYNQMMIO
from pynq import Bitstream
import pynq.lib.dma

# Import nostri moduli
from pr_zone_manager import PRZoneManager
from dfx_decoupler_manager import DFXDecouplerManager

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
    """Resource Manager che usa PYNQ hardware reale con supporto PR zones e DFX"""
    
    def __init__(self, tenant_manager, config_manager=None):
        self.tenant_manager = tenant_manager
        self.config_manager = config_manager
        self._resources: Dict[str, ManagedResource] = {}
        self._overlays: Dict[str, PYNQOverlay] = {}
        self._mmios: Dict[str, PYNQMMIO] = {}
        self._buffers: Dict[str, any] = {}
        self._dmas: Dict[str, any] = {}
        self._lock = threading.RLock()
        
        # Directory bitstream
        self.bitstream_dir = '/home/xilinx/bitstreams'
        if config_manager:
            self.bitstream_dir = config_manager.bitstream_dir
        
        # Carica shell statica (full.bit)
        self.static_overlay = None
        self._initialize_static_overlay()
        
        # Inizializza PR Zone Manager
        num_pr_zones = 2  # Default
        if config_manager and hasattr(config_manager, 'num_pr_zones'):
            num_pr_zones = config_manager.num_pr_zones
        
        self.pr_zone_manager = PRZoneManager(num_pr_zones)
        
        # Inizializza DFX Decoupler Manager
        self.dfx_manager = DFXDecouplerManager(self.static_overlay)
        self._initialize_dfx_decouplers()
        
        # Mappa degli indirizzi per PR zone
        self.pr_zone_addresses = {}
        self._initialize_pr_zone_addresses()
        
        
        #Gestione char device
        
        self._char_devices: Dict[str, str] = {}  # tenant_id -> device_path
        self._buffer_offsets: Dict[str, int] = {}  # tenant_id -> next_offset
        self._buffer_to_offset: Dict[str, int] = {}  # buffer_handle -> vm_offset
        
       
        self._verify_char_device_support()
        
        logger.info(f"[PYNQ] Initialized Resource Manager with char device support")
        
        # Crea event loop per PYNQ se non esiste
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        
        logger.info(f"[PYNQ] Initialized Resource Manager with {num_pr_zones} PR zones and DFX support")
    
    def _initialize_static_overlay(self):
        """Carica la shell statica all'avvio"""
        static_bitstream = '/home/xilinx/bitstreams/full.bit'
        if self.config_manager and hasattr(self.config_manager, 'static_bitstream'):
            static_bitstream = self.config_manager.static_bitstream
        
        logger.info(f"[PYNQ] Loading static overlay: {static_bitstream}")
        
        try:
            # Carica in un thread separato per evitare problemi con event loop
            def load_static():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    self.static_overlay = PYNQOverlay(static_bitstream)
                finally:
                    loop.close()
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(load_static)
                future.result()
            
            logger.info("[PYNQ] Static overlay loaded successfully")

            logger.info("[PYNQ] Static overlay details: ", dir(self.static_overlay) )
            
            
        except Exception as e:
            logger.error(f"[PYNQ] Failed to load static overlay: {e}")
            raise RuntimeError(f"Cannot initialize without static overlay: {e}")
    
    def _initialize_dfx_decouplers(self):
        """Inizializza i DFX decouplers GPIO dalla configurazione"""
        if not self.config_manager:
            # Default configuration per 2 zone
            self.dfx_manager.register_decoupler(0, gpio_pin=0)  # PR Zone 0 usa GPIO 0
            self.dfx_manager.register_decoupler(1, gpio_pin=1)  # PR Zone 1 usa GPIO 1
            logger.warning("[PYNQ] Registered DEFAULT GPIO decouplers (zones 0-1)")
            return
        
        # Leggi configurazione PR zones
        if hasattr(self.config_manager, 'global_config') and 'pr_zones' in self.config_manager.global_config:
            pr_zones_config = self.config_manager.global_config['pr_zones']
        else:
            pr_zones_config = getattr(self.config_manager, 'pr_zones', [])
        
        for zone_config in pr_zones_config:
            zone_id = zone_config.get('zone_id') if isinstance(zone_config, dict) else zone_config.zone_id
            
            # Cerca gpio_pin nella config, altrimenti usa zone_id
            gpio_pin = None
            if isinstance(zone_config, dict):
                gpio_pin = zone_config.get('gpio_pin', zone_id)
            elif hasattr(zone_config, 'gpio_pin'):
                gpio_pin = zone_config.gpio_pin
            else:
                gpio_pin = zone_id
            
            if zone_id is not None:
                self.dfx_manager.register_decoupler(zone_id, gpio_pin=gpio_pin)
                logger.info(f"[PYNQ] Registered GPIO decoupler for zone {zone_id} on pin {gpio_pin}")
        
        # Assicurati che tutte le zone siano accoppiate all'avvio
        self.dfx_manager.ensure_all_coupled()
    
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
    
    def _run_in_loop(self, coro):
        """Esegue coroutine nel loop asyncio"""
        try:
            if asyncio.get_running_loop() == self._loop:
                return asyncio.create_task(coro).result()
        except RuntimeError:
            pass
        
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()
    
    def load_overlay(self, tenant_id: str, bitfile_path: str) -> Tuple[str, Dict]:
        """
        Carica overlay parziale con gestione DFX e PR zones.
        """
        with self._lock:
            if not self.tenant_manager.can_allocate_overlay(tenant_id):
                raise Exception("Overlay limit reached")
            
            tenant_config = self.tenant_manager.config.get(tenant_id)
            if not tenant_config:
                raise Exception(f"Tenant {tenant_id} not found")
            
            allowed_bitstreams = tenant_config.allowed_bitstreams or set()
            
            result = self.pr_zone_manager.find_best_zone_for_bitstream(
                bitfile_path,
                tenant_id,
                self.bitstream_dir,
                allowed_bitstreams
            )
            
            if not result:
                raise Exception(f"No available PR zone for bitstream {bitfile_path}")
            
            zone_id, actual_bitstream_path = result
            
            if hasattr(tenant_config, 'allowed_pr_zones'):
                if zone_id not in tenant_config.allowed_pr_zones:
                    raise Exception(f"Tenant {tenant_id} not allowed to use PR zone {zone_id}")
            
            logger.info(f"[PYNQ] Loading partial bitstream {actual_bitstream_path} "
                    f"in PR zone {zone_id} for tenant {tenant_id}")
            
            success = self.dfx_manager.reconfigure_pr_zone(zone_id, actual_bitstream_path)
            if not success:
                raise Exception(f"Failed to reconfigure PR zone {zone_id}")
            
            handle = self._generate_handle("overlay")
            
            if not self.pr_zone_manager.allocate_zone(
                tenant_id, zone_id, actual_bitstream_path, handle
            ):
                raise Exception(f"Failed to allocate PR zone {zone_id}")
            
            # NUOVO: Imposta permessi sul device UIO
            uio_device = f"/dev/uio{zone_id}"
            if os.path.exists(uio_device):
                try:
                    os.chmod(uio_device, 0o660)
                    os.chown(uio_device, tenant_config.uid, tenant_config.gid)
                    logger.info(f"Set permissions on {uio_device} for tenant {tenant_id}")
                except Exception as e:
                    logger.warning(f"Could not set permissions on {uio_device}: {e}")
            
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="overlay",
                created_at=time.time(),
                metadata={
                    "bitfile": actual_bitstream_path,
                    "pr_zone": zone_id,
                    "requested_bitfile": bitfile_path,
                    "partial": True,
                    "uio_device": uio_device  # NUOVO: salva path UIO
                },
                pynq_object=None
            )
            
            self.tenant_manager.resources[tenant_id].overlays.add(handle)
            
            ip_cores = self._get_pr_zone_ip_cores(zone_id)
            # NUOVO: Aggiungi info zona ai metadata degli IP cores
            ip_cores['_zone_id'] = zone_id
            ip_cores['_uio_device'] = uio_device
            
            logger.info(f"[PYNQ] Partial bitstream loaded successfully: {handle} "
                    f"in PR zone {zone_id} with {len(ip_cores)-2} accessible IPs")  # -2 per i metadata
            
            return handle, ip_cores
    
    def _get_pr_zone_ip_cores(self, zone_id: int) -> Dict:
        """
        Ottieni gli IP cores per una specifica PR zone.
        Questo dovrebbe essere configurato in base al tuo design.
        """
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
        """Ottieni l'oggetto IP PYNQ reale per interazioni dirette"""
        with self._lock:
            # Verifica che l'overlay appartenga al tenant
            if overlay_handle not in self._resources:
                raise Exception("Overlay handle not found")
            
            resource = self._resources[overlay_handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
            # Per bitstream parziali, non c'è un vero oggetto IP PYNQ
            # Ritorna None o crea un wrapper
            if resource.metadata.get('partial', False):
                logger.warning(f"IP objects not available for partial bitstreams")
                return None
            
            # Per overlay completi (non implementato in questa versione)
            raise NotImplementedError("Full overlay IP objects not implemented")
    
    def create_mmio(self, tenant_id: str, base_address: int, length: int) -> str:
        """Crea MMIO verificando che l'indirizzo sia permesso per le PR zones del tenant"""
        with self._lock:
            # Prima verifica quale PR zone sta usando il tenant
            tenant_zones = self.pr_zone_manager.get_tenant_zones(tenant_id)
            
            # Verifica che l'indirizzo sia permesso per almeno una delle zone del tenant
            address_allowed = False
            allowed_zone = None
            
            for zone_id in tenant_zones:
                zone_addresses = self.pr_zone_addresses.get(zone_id, [])
                for allowed_base, allowed_size in zone_addresses:
                    # Verifica se l'indirizzo richiesto è dentro il range permesso
                    if (base_address >= allowed_base and 
                        base_address + length <= allowed_base + allowed_size):
                        address_allowed = True
                        allowed_zone = zone_id
                        break
                if address_allowed:
                    break
            
            if not address_allowed:
                # Se il tenant non ha zone allocate, nega l'accesso
                if not tenant_zones:
                    raise Exception(f"Tenant {tenant_id} has no PR zones allocated")
                
                # Log dettagliato per debug
                logger.warning(f"Address 0x{base_address:08x} not allowed for tenant {tenant_id}")
                logger.warning(f"Tenant zones: {tenant_zones}")
                for zone_id in tenant_zones:
                    logger.warning(f"  Zone {zone_id} addresses: {self.pr_zone_addresses.get(zone_id, [])}")
                
                raise Exception(f"Address 0x{base_address:08x} not allowed for tenant's PR zones")
            
            logger.info(f"[PYNQ] Creating MMIO at 0x{base_address:08x} for zone {allowed_zone}")
            
            # Crea MMIO PYNQ reale
            try:
                mmio = PYNQMMIO(base_address, length)
            except Exception as e:
                logger.error(f"[PYNQ] Failed to create MMIO: {e}")
                raise Exception(f"Failed to create MMIO: {e}")
            
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
                    "length": length,
                    "pr_zone": allowed_zone
                },
                pynq_object=mmio
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].mmio_handles.add(handle)
            
            logger.info(f"[PYNQ] MMIO created: {handle} for tenant {tenant_id} at 0x{base_address:08x}")
            return handle
    
    def mmio_read(self, tenant_id: str, handle: str, offset: int, length: int = 4) -> int:
        """Legge da MMIO hardware reale con controlli di sicurezza"""
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
            
            # Ottieni oggetto MMIO PYNQ
            mmio = self._mmios.get(handle)
            if not mmio:
                raise Exception("MMIO object not found")
            
            # Leggi valore dall'hardware
            value = mmio.read(offset, length)
            
            logger.debug(f"[PYNQ] MMIO read by {tenant_id}: handle={handle}, offset=0x{offset:04x}, value=0x{value:08x}")
            return value

    def mmio_write(self, tenant_id: str, handle: str, offset: int, value: int):
        """Scrive su MMIO hardware reale con controlli di sicurezza"""
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
            
            # Assumiamo scritture a 32-bit (4 bytes) per default
            length = 4
            
            # Verifica che la scrittura non ecceda i limiti
            if offset + length > mmio_length:
                raise Exception(f"Write would exceed MMIO bounds: offset {offset} + {length} > MMIO size {mmio_length}")
            
            # Verifica che il valore sia nel range 32-bit
            if value < 0 or value > 0xFFFFFFFF:
                raise Exception(f"Value {value} out of range for 32-bit write")
            
            # Ottieni oggetto MMIO PYNQ
            mmio = self._mmios.get(handle)
            if not mmio:
                raise Exception("MMIO object not found")
            
            # Scrivi valore sull'hardware
            mmio.write(offset, value)
            
            logger.debug(f"[PYNQ] MMIO write by {tenant_id}: handle={handle}, offset=0x{offset:04x}, value=0x{value:08x}")
    
    def allocate_buffer(self, tenant_id: str, shape, dtype='uint8') -> Dict:
        """Alloca buffer su hardware PYNQ reale E registra nel char device"""
        with self._lock:
            # Calcola size
            np_shape = tuple(shape) if isinstance(shape, (list, tuple)) else (shape,)
            np_dtype = np.dtype(dtype)
            size = np.prod(np_shape) * np_dtype.itemsize
            
            # Verifica limiti tenant
            if not self.tenant_manager.can_allocate_buffer(tenant_id, size):
                raise Exception("Buffer allocation limit reached")
            
            # Alloca buffer PYNQ reale
            try:
                buffer = pynq_allocate(shape=np_shape, dtype=np_dtype)
                physical_address = buffer.physical_address
                
            except Exception as e:
                logger.error(f"[PYNQ] Failed to allocate buffer: {e}")
                raise Exception(f"Failed to allocate buffer: {e}")
            
            # Genera handle
            handle = self._generate_handle("buffer")
            
            # NUOVO: Registra nel char device se disponibile
            vm_offset = None
            if self._char_device_enabled and tenant_id in self._char_devices:
                vm_offset = self._register_buffer_in_char_device(
                    tenant_id, 
                    handle,
                    physical_address,
                    buffer.nbytes
                )
            
            # Salva riferimenti
            self._buffers[handle] = buffer
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
                    "vm_offset": vm_offset  # NUOVO: offset nel char device
                },
                pynq_object=buffer
            )
            
            # Aggiorna contatori tenant
            self.tenant_manager.resources[tenant_id].buffer_handles.add(handle)
            self.tenant_manager.resources[tenant_id].total_memory_bytes += size
            
            logger.info(f"[PYNQ] Buffer allocated: {handle}, phys=0x{physical_address:08x}, "
                       f"size={size}, vm_offset=0x{vm_offset:x if vm_offset else 0}")
            
            return {
                'handle': handle,
                'physical_address': physical_address,
                'total_size': size,
                'shm_name': None,
                'shape': np_shape,
                'dtype': str(np_dtype),
                'vm_offset': vm_offset,  # NUOVO: per il container
                'char_device': self._char_devices.get(tenant_id)  # NUOVO: path del device
            }
    
    def _register_buffer_in_char_device(self, tenant_id: str, buffer_id: str, 
                                       phys_addr: int, size: int) -> int:

        
        if tenant_id not in self._buffer_offsets:
            self._buffer_offsets[tenant_id] = 0
        
        vm_offset = self._buffer_offsets[tenant_id]
        

        sysfs_path = f"/sys/devices/virtual/pynq_char/pynq_mem_{tenant_id}/add_buffer"
        
        command = f"{vm_offset:x},{phys_addr:x},{size:x},{buffer_id}"
        
        try:
            with open(sysfs_path, 'w') as f:
                f.write(command + '\n')
        except Exception as e:
            logger.error(f"[CHAR_DEV] Failed to register buffer: {e}")
            raise
        
        # Salva mapping
        self._buffer_to_offset[buffer_id] = vm_offset
        
        # Incrementa offset (allineato a pagina)
        PAGE_SIZE = 4096
        self._buffer_offsets[tenant_id] += ((size + PAGE_SIZE - 1) // PAGE_SIZE) * PAGE_SIZE
        
        logger.info(f"[CHAR_DEV] Registered buffer {buffer_id} at offset 0x{vm_offset:x}")
        
        return vm_offset
    
    
    
    def read_buffer(self, tenant_id: str, handle: str, offset: int, length: int) -> bytes:
        """Leggi dati da buffer PYNQ"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Ottieni buffer PYNQ
            buffer = self._buffers.get(handle)
            if buffer is None:
                raise Exception("Buffer object not found")
            
            # Verifica limiti
            buffer_size = resource.metadata['size']
            if offset < 0 or offset >= buffer_size:
                raise Exception(f"Offset {offset} out of bounds [0, {buffer_size})")
            
            if offset + length > buffer_size:
                raise Exception(f"Read would exceed buffer bounds")
            
            # Leggi dati
            data_bytes = buffer.tobytes()[offset:offset+length]
            
            logger.debug(f"[PYNQ] Buffer read: handle={handle}, offset={offset}, length={length}")
            return data_bytes

    def write_buffer(self, tenant_id: str, handle: str, data: bytes, offset: int):
        """Scrivi dati in buffer PYNQ"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Ottieni buffer PYNQ
            buffer = self._buffers.get(handle)
            if buffer is None:
                raise Exception("Buffer object not found")
            
            # Verifica limiti
            buffer_size = resource.metadata['size']
            data_length = len(data)
            
            if offset < 0 or offset >= buffer_size:
                raise Exception(f"Offset {offset} out of bounds [0, {buffer_size})")
            
            if offset + data_length > buffer_size:
                raise Exception(f"Write would exceed buffer bounds")
            
            # Scrivi dati nel buffer
            dtype = np.dtype(resource.metadata['dtype'])
            temp_array = np.frombuffer(data, dtype=dtype)
            
            # Calcola indici per il buffer
            start_idx = offset // dtype.itemsize
            end_idx = start_idx + len(temp_array)
            
            # Scrivi nel buffer PYNQ
            buffer.flat[start_idx:end_idx] = temp_array
            
            # Assicura che i dati siano sincronizzati con la memoria fisica
            buffer.flush()
            
            logger.debug(f"[PYNQ] Buffer write: handle={handle}, offset={offset}, length={data_length}")

    def free_buffer(self, tenant_id: str, handle: str):
        """Libera un buffer PYNQ e rimuovi dal char device"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Buffer handle not found")
                
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Buffer not owned by tenant")
            
            # Ottieni buffer
            buffer = self._buffers.get(handle)
            if buffer:
                size = resource.metadata['size']
                
                # NUOVO: Rimuovi dal char device
                if self._char_device_enabled and handle in self._buffer_to_offset:
                    vm_offset = self._buffer_to_offset[handle]
                    sysfs_remove = f"/sys/devices/virtual/pynq_char/pynq_mem_{tenant_id}/remove_buffer"
                    
                    try:
                        with open(sysfs_remove, 'w') as f:
                            f.write(f"{vm_offset:x}\n")
                        
                        del self._buffer_to_offset[handle]
                        logger.info(f"[CHAR_DEV] Removed buffer {handle} from char device")
                    except:
                        logger.warning(f"[CHAR_DEV] Could not remove buffer from char device")
                
                # Aggiorna contatori tenant
                self.tenant_manager.resources[tenant_id].buffer_handles.discard(handle)
                self.tenant_manager.resources[tenant_id].total_memory_bytes -= size
                
                # Libera buffer PYNQ
                if hasattr(buffer, 'freebuffer'):
                    buffer.freebuffer()
                
                # Rimuovi riferimenti
                del self._buffers[handle]
                del self._resources[handle]
                
                logger.info(f"[PYNQ] Buffer freed: {handle}, size={size} bytes")
    
    def create_dma(self, tenant_id: str, dma_name: str) -> Tuple[str, Dict]:
        """Crea DMA handle per un DMA nella PR zone del tenant"""
        with self._lock:
            # Verifica che il tenant abbia almeno una PR zone allocata
            tenant_zones = self.pr_zone_manager.get_tenant_zones(tenant_id)
            if not tenant_zones:
                raise Exception(f"Tenant {tenant_id} has no PR zones allocated")
            
            # Per ora assumiamo che il DMA sia nella prima zona del tenant
            # In un design reale, dovresti mappare i DMA alle zone specifiche
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
        """Scarica overlay parziale e libera la PR zone"""
        with self._lock:
            # Verifica ownership
            if handle not in self._resources:
                raise Exception("Overlay handle not found")
            
            resource = self._resources[handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
            # Ottieni zona PR dal metadata
            zone_id = resource.metadata.get('pr_zone')
            
            if zone_id is not None:
                # Per sicurezza, decouple la zona prima di rilasciarla
                try:
                    self.dfx_manager.decouple_zone(zone_id)
                    time.sleep(0.1)
                except Exception as e:
                    logger.warning(f"[PYNQ] Error decoupling zone {zone_id}: {e}")
            
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
        
        # Aggiungi info PR zones - converti in lista se necessario
        tenant_zones = self.pr_zone_manager.get_tenant_zones(tenant_id)
        if isinstance(tenant_zones, set):
            resources['pr_zones'] = list(tenant_zones)
        else:
            resources['pr_zones'] = tenant_zones
        
        return resources

    def get_pr_zone_status(self) -> Dict:
        """Ottieni stato delle PR zones incluso stato DFX"""
        base_info = self.pr_zone_manager.get_allocation_info()
        
        # Aggiungi stato DFX
        for zone_id in range(self.pr_zone_manager.num_pr_zones):
            zone_key = f'PR_{zone_id}'
            if zone_key in base_info['allocations']:
                base_info['allocations'][zone_key]['decoupled'] = \
                    self.dfx_manager.is_decoupled(zone_id)
            
            # Aggiungi indirizzi della zona
            base_info['allocations'][zone_key] = base_info['allocations'].get(zone_key, {})
            base_info['allocations'][zone_key]['addresses'] = \
                self.pr_zone_addresses.get(zone_id, [])
        
        return base_info

    def cleanup_tenant_resources(self, tenant_id: str):
        """Pulisce tutte le risorse di un tenant incluse le PR zones"""
        with self._lock:
            # Prima rilascia tutte le PR zones del tenant
            released_zones = self.pr_zone_manager.release_all_tenant_zones(tenant_id)
            if released_zones:
                logger.info(f"[PYNQ] Released PR zones {released_zones} for tenant {tenant_id}")
                
                # Decouple le zone rilasciate per sicurezza
                for zone_id in released_zones:
                    try:
                        self.dfx_manager.decouple_zone(zone_id)
                    except Exception as e:
                        logger.warning(f"[PYNQ] Error decoupling zone {zone_id}: {e}")
            
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
        """Pulisce una singola risorsa su hardware PYNQ"""
        if handle not in self._resources:
            return
            
        resource = self._resources[handle]
        
        try:
            if resource.resource_type == "overlay":
                # Overlay/bitstream parziali - già gestiti sopra
                logger.info(f"[PYNQ] Cleaned overlay: {handle}")
                
            elif resource.resource_type == "mmio":
                # MMIO viene pulito automaticamente
                if handle in self._mmios:
                    del self._mmios[handle]
                logger.info(f"[PYNQ] Cleaned MMIO: {handle}")
                
            elif resource.resource_type == "buffer":
                # Buffer PYNQ
                buffer = self._buffers.get(handle)
                if buffer is not None:  # FIX: usa 'is not None' invece di 'if buffer'
                    try:
                        # PYNQ buffers non hanno sempre freebuffer, dipende dalla versione
                        if hasattr(buffer, 'freebuffer') and callable(buffer.freebuffer):
                            buffer.freebuffer()
                        elif hasattr(buffer, 'close') and callable(buffer.close):
                            buffer.close()
                        # Se nessuno dei due metodi esiste, il buffer verrà rilasciato dal GC
                    except Exception as e:
                        logger.warning(f"[PYNQ] Error freeing buffer {handle}: {e}")
                    
                    # Aggiorna contatori tenant
                    size = resource.metadata.get('size', 0)
                    tenant_resources = self.tenant_manager.resources.get(resource.tenant_id)
                    if tenant_resources:
                        tenant_resources.buffer_handles.discard(handle)
                        tenant_resources.total_memory_bytes -= size
                    
                    # Rimuovi riferimento
                    if handle in self._buffers:
                        del self._buffers[handle]
                    logger.info(f"[PYNQ] Cleaned buffer: {handle}")
                    
            elif resource.resource_type == "dma":
                # DMA viene pulito automaticamente
                if handle in self._dmas:
                    del self._dmas[handle]
                logger.info(f"[PYNQ] Cleaned DMA: {handle}")
            
            # Rimuovi dai registri del tenant manager
            tenant_resources = self.tenant_manager.resources.get(resource.tenant_id)
            if tenant_resources:
                if resource.resource_type == "overlay":
                    tenant_resources.overlays.discard(handle)
                elif resource.resource_type == "mmio":
                    tenant_resources.mmio_handles.discard(handle)
                elif resource.resource_type == "dma":
                    tenant_resources.dma_handles.discard(handle)
                # buffer_handles già gestito sopra
            
        except Exception as e:
            logger.error(f"[PYNQ] Error cleaning up {resource.resource_type} {handle}: {e}")
            import traceback
            logger.error(traceback.format_exc())
        
        # Rimuovi dai registri
        del self._resources[handle]
        
        
    def _verify_char_device_support(self):
        """Verifica che il kernel module pynq_char_mapper sia caricato"""
        if not os.path.exists('/sys/class/pynq_char/create_device'):
            logger.warning("[CHAR_DEV] Kernel module not loaded - char device support disabled")
            logger.warning("[CHAR_DEV] Load with: sudo insmod pynq_char_mapper.ko")
            self._char_device_enabled = False
        else:
            self._char_device_enabled = True
            logger.info("[CHAR_DEV] Kernel module detected - char device support enabled")
    
    def create_tenant_char_device(self, tenant_id: str) -> str:
        """
        Crea char device per un tenant (chiamato al setup iniziale).
        
        Returns:
            Path del device creato (es. /dev/pynq_mem_tenant1)
        """
        if not self._char_device_enabled:
            raise Exception("Char device support not available - kernel module not loaded")
        
        with self._lock:
            # Se già esiste, ritorna quello
            if tenant_id in self._char_devices:
                return self._char_devices[tenant_id]
            
            # Crea device tramite sysfs
            try:
                with open('/sys/class/pynq_char/create_device', 'w') as f:
                    f.write(f"{tenant_id}\n")
                
                time.sleep(0.2)  # Attendi creazione
                
                device_path = f"/dev/pynq_mem_{tenant_id}"
                
                if not os.path.exists(device_path):
                    raise Exception(f"Device {device_path} not created")
                
                # Cambia permessi
                tenant_config = self.config_manager.tenants.get(tenant_id)
                if tenant_config:
                    os.chmod(device_path, 0o660)
                    try:
                        os.chown(device_path, tenant_config.uid, tenant_config.gid)
                    except:
                        logger.warning(f"Could not chown {device_path}")
                
                # Salva riferimenti
                self._char_devices[tenant_id] = device_path
                self._buffer_offsets[tenant_id] = 0
                
                logger.info(f"[CHAR_DEV] Created device {device_path} for tenant {tenant_id}")
                return device_path
                
            except Exception as e:
                logger.error(f"[CHAR_DEV] Failed to create device for {tenant_id}: {e}")
                raise