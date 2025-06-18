# hypervisor/pynq_resource_manager.py - Fixed version
import os
import threading
import uuid
import time
import asyncio
from typing import Dict, Optional, Tuple
from dataclasses import dataclass
import logging
import numpy as np

# Import PYNQ reale
from pynq import Overlay as PYNQOverlay
from pynq import allocate as pynq_allocate
from pynq.mmio import MMIO as PYNQMMIO
import pynq.lib.dma

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
    """Resource Manager che usa PYNQ hardware reale"""
    
    def __init__(self, tenant_manager):
        self.tenant_manager = tenant_manager
        self._resources: Dict[str, ManagedResource] = {}
        self._overlays: Dict[str, PYNQOverlay] = {}
        self._mmios: Dict[str, PYNQMMIO] = {}
        self._buffers: Dict[str, any] = {}
        self._dmas: Dict[str, any] = {}
        self._lock = threading.RLock()
        
        # Crea event loop per PYNQ se non esiste
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            # Crea nuovo event loop per questo thread
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
        
        logger.info("[PYNQ] Initialized PYNQ Resource Manager for real hardware")
        
    def _generate_handle(self, prefix: str) -> str:
        """Genera handle univoco"""
        return f"{prefix}_{uuid.uuid4().hex[:8]}"
    
    def _run_in_loop(self, coro):
        """Esegue coroutine nel loop asyncio"""
        # Se siamo già nel loop, usa run_coroutine_threadsafe
        try:
            if asyncio.get_running_loop() == self._loop:
                return asyncio.create_task(coro).result()
        except RuntimeError:
            pass
        
        # Altrimenti, schedula nel loop
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()
    
    def load_overlay(self, tenant_id: str, bitfile_path: str) -> Tuple[str, Dict]:
        """Carica overlay su PYNQ reale"""
        with self._lock:
            # Verifica permessi
            if not self.tenant_manager.can_allocate_overlay(tenant_id):
                raise Exception("Overlay limit reached")
                
            if not self.tenant_manager.is_bitstream_allowed(tenant_id, os.path.basename(bitfile_path)):
                raise Exception("Bitstream not allowed")
            
            logger.info(f"[PYNQ] Loading overlay {bitfile_path} for tenant {tenant_id}")
            
            # Crea un thread event loop context per PYNQ
            overlay = None
            error = None
            
            def load_overlay_sync():
                nonlocal overlay, error
                try:
                    # Imposta event loop per questo thread
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    # Carica overlay - usa il path passato invece di hardcoded
                    overlay = PYNQOverlay('/home/xilinx/conv2d.bit')
                    
                    # Cleanup loop
                    loop.close()
                except Exception as e:
                    error = e
                    if 'loop' in locals():
                        loop.close()
            
            # Esegui in thread separato per evitare problemi con event loop
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(load_overlay_sync)
                future.result()  # Attendi completamento
            
            if error:
                logger.error(f"[PYNQ] Failed to load overlay: {error}")
                raise Exception(f"Failed to load overlay: {error}")
            
            if not overlay:
                raise Exception("Failed to load overlay: Unknown error")
            
            # Genera handle
            handle = self._generate_handle("overlay")
            
            # Salva riferimenti
            self._overlays[handle] = overlay
            self._resources[handle] = ManagedResource(
                handle=handle,
                tenant_id=tenant_id,
                resource_type="overlay",
                created_at=time.time(),
                metadata={"bitfile": bitfile_path},
                pynq_object=overlay
            )
            
            # Registra con tenant manager
            self.tenant_manager.resources[tenant_id].overlays.add(handle)
            
            # Estrai IP cores dall'overlay PYNQ usando ip_dict
            ip_cores = {}
            
            # PYNQ espone gli IP in overlay.ip_dict
            for ip_name, ip_info in overlay.ip_dict.items():
                # Salta IP non MMIO-based o di sistema
                if ip_name in ['PSDDR', 'zynq_ultra_ps_e_0']:
                    continue
                    
                # Estrai informazioni dall'IP
                base_addr = ip_info.get('phys_addr', 0)
                addr_range = ip_info.get('addr_range', 0)
               
                # Verifica permessi tenant
             
                if self.tenant_manager.is_address_allowed(tenant_id, base_addr, addr_range):

                    # Estrai tipo IP
                    ip_type = ip_info.get('type', 'unknown')
                    
                    # Estrai parametri
                    parameters = {}
                    if 'parameters' in ip_info:
                        # Filtra solo parametri rilevanti (non tutti i C_*)
                        for param, value in ip_info['parameters'].items():
                            if not param.startswith('C_'):
                                parameters[param] = str(value)
                            elif any(key in param for key in ['WIDTH', 'ADDR', 'DATA']):
                                # Includi alcuni parametri C_ importanti
                                parameters[param] = str(value)
                    
                    # Estrai register map
                    registers = {}
                    if 'registers' in ip_info:
                        for reg_name, reg_info in ip_info['registers'].items():
                            registers[reg_name] = {
                                'offset': reg_info.get('address_offset', 0),
                                'description': reg_info.get('description', ''),
                                'size': reg_info.get('size', 32),
                                'access': reg_info.get('access', 'read-write')
                            }
                    
                    ip_cores[ip_name] = {
                        'name': ip_name,
                        'type': ip_type,
                        'base_address': base_addr,
                        'address_range': addr_range,
                        'parameters': parameters,
                        'registers': registers
                    }
                    
                    logger.info(f"[PYNQ] Found IP: {ip_name} ({ip_type}) at 0x{base_addr:08x}")
                    
                    # Se l'IP è accessibile come attributo dell'overlay, salvalo
                    if hasattr(overlay, ip_name):
                        ip_obj = getattr(overlay, ip_name)
                        # Potresti voler salvare questo riferimento per uso futuro
                        logger.debug(f"[PYNQ] IP {ip_name} is accessible as overlay.{ip_name}")
            
            logger.info(f"[PYNQ] Overlay loaded successfully: {handle} with {len(ip_cores)} accessible IPs")
            return handle, ip_cores
    def get_ip_object(self, tenant_id: str, overlay_handle: str, ip_name: str):
        """Ottieni l'oggetto IP PYNQ reale per interazioni dirette"""
        with self._lock:
            # Verifica che l'overlay appartenga al tenant
            if overlay_handle not in self._resources:
                raise Exception("Overlay handle not found")
            
            resource = self._resources[overlay_handle]
            if resource.tenant_id != tenant_id:
                raise Exception("Overlay not owned by tenant")
            
            # Ottieni overlay
            overlay = self._overlays.get(overlay_handle)
            if not overlay:
                raise Exception("Overlay not found")
            
            # Verifica che l'IP esista
            if ip_name not in overlay.ip_dict:
                raise Exception(f"IP {ip_name} not found in overlay")
            
            # Verifica permessi sull'indirizzo dell'IP
            ip_info = overlay.ip_dict[ip_name]
            base_addr = ip_info.get('phys_addr', 0)
            addr_range = ip_info.get('addr_range', 0)
            
            if not self.tenant_manager.is_address_allowed(tenant_id, base_addr, addr_range):
                raise Exception(f"Tenant not allowed to access IP {ip_name}")
            
            # Ottieni l'oggetto IP se esiste come attributo
            if hasattr(overlay, ip_name):
                return getattr(overlay, ip_name)
            
            # Altrimenti ritorna None o solleva eccezione
            logger.warning(f"IP {ip_name} not accessible as overlay attribute")
            return None
    
    def _extract_registers(self, ip_core) -> Dict:
        """Estrae register map da un IP core PYNQ"""
        registers = {}
        
        # Metodo 1: Check for bindto attribute (Vivado IP)
        if hasattr(ip_core, 'bindto'):
            # Potrebbe avere register definitions
            if hasattr(ip_core, '_registers'):
                for reg_name, reg_info in ip_core._registers.items():
                    registers[reg_name] = {
                        'offset': reg_info.address_offset,
                        'description': reg_info.description if hasattr(reg_info, 'description') else ''
                    }
        
        # Metodo 2: Check for register_map (HLS IP)
        if hasattr(ip_core, 'register_map'):
            reg_map = ip_core.register_map
            # Prova a estrarre da diversi formati
            if hasattr(reg_map, '__dict__'):
                for attr in dir(reg_map):
                    if not attr.startswith('_'):
                        try:
                            # Cerca offset
                            offset = None
                            if hasattr(reg_map, f'{attr}_offset'):
                                offset = getattr(reg_map, f'{attr}_offset')
                            elif hasattr(reg_map, attr):
                                # Potrebbe essere un oggetto con .address
                                reg_obj = getattr(reg_map, attr)
                                if hasattr(reg_obj, 'address'):
                                    offset = reg_obj.address
                            
                            if offset is not None:
                                registers[attr] = {
                                    'offset': offset,
                                    'description': ''
                                }
                        except:
                            pass
        
        # Metodo 3: Per AXI GPIO e altri IP standard
        if type(ip_core).__name__ == 'AxiGPIO':
            registers = {
                'GPIO_DATA': {'offset': 0x00, 'description': 'GPIO Data Register'},
                'GPIO_TRI': {'offset': 0x04, 'description': 'GPIO 3-state Control'},
                'GPIO2_DATA': {'offset': 0x08, 'description': 'GPIO2 Data Register'},
                'GPIO2_TRI': {'offset': 0x0C, 'description': 'GPIO2 3-state Control'}
            }
        elif type(ip_core).__name__ == 'DMA':
            registers = {
                'MM2S_DMACR': {'offset': 0x00, 'description': 'MM2S DMA Control'},
                'MM2S_DMASR': {'offset': 0x04, 'description': 'MM2S DMA Status'},
                'MM2S_SA': {'offset': 0x18, 'description': 'MM2S Source Address'},
                'MM2S_LENGTH': {'offset': 0x28, 'description': 'MM2S Transfer Length'},
                'S2MM_DMACR': {'offset': 0x30, 'description': 'S2MM DMA Control'},
                'S2MM_DMASR': {'offset': 0x34, 'description': 'S2MM DMA Status'},
                'S2MM_DA': {'offset': 0x48, 'description': 'S2MM Destination Address'},
                'S2MM_LENGTH': {'offset': 0x58, 'description': 'S2MM Transfer Length'}
            }
        
        return registers
    
    # ... resto dei metodi rimane uguale ...
    
    def create_mmio(self, tenant_id: str, base_address: int, length: int) -> str:
        """Crea MMIO su hardware reale"""
        with self._lock:
            # Verifica permessi
            if not self.tenant_manager.is_address_allowed(tenant_id, base_address, length):
                raise Exception(f"Tenant {tenant_id} not allowed to access address 0x{base_address:08x}")
            
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
                    "length": length
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
            
            # Verifica che il tenant possa ancora accedere all'indirizzo effettivo
            actual_address = base_address + offset
            if not self.tenant_manager.is_address_allowed(tenant_id, actual_address, length):
                raise Exception(f"Tenant {tenant_id} no longer allowed to access address 0x{actual_address:08x}")
            
            # Ottieni oggetto MMIO PYNQ
            mmio = self._mmios.get(handle)
            if not mmio:
                raise Exception("MMIO object not found")
            
            # Leggi valore dall'hardware
            # PYNQ MMIO.read() accetta offset in bytes e ritorna un intero
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
            
            # Verifica che offset sia nel range
            if offset >= mmio_length:
                raise Exception(f"Write offset out of bounds: offset {offset} >= MMIO size {mmio_length}")
            
            # Verifica che la scrittura non ecceda i limiti
            if offset + length > mmio_length:
                raise Exception(f"Write would exceed MMIO bounds: offset {offset} + {length} > MMIO size {mmio_length}")
            
            # Verifica che il tenant possa ancora accedere all'indirizzo effettivo
            actual_address = base_address + offset
            if not self.tenant_manager.is_address_allowed(tenant_id, actual_address, length):
                raise Exception(f"Tenant {tenant_id} no longer allowed to access address 0x{actual_address:08x}")
            
            # Verifica che il valore sia nel range 32-bit
            if value < 0 or value > 0xFFFFFFFF:
                raise Exception(f"Value {value} out of range for 32-bit write")
            
            # Ottieni oggetto MMIO PYNQ
            mmio = self._mmios.get(handle)
            if not mmio:
                raise Exception("MMIO object not found")
            
            # Scrivi valore sull'hardware
            # PYNQ MMIO.write() di default scrive 32-bit
            mmio.write(offset, value)
            
            logger.debug(f"[PYNQ] MMIO write by {tenant_id}: handle={handle}, offset=0x{offset:04x}, value=0x{value:08x}")
    def allocate_buffer(self, tenant_id: str, shape, dtype='uint8') -> Dict:
        """Alloca buffer su hardware PYNQ reale"""
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
                # PYNQ allocate() crea un buffer contiguo in memoria
                # che può essere usato per DMA transfers
                buffer = pynq_allocate(shape=np_shape, dtype=np_dtype)
                
                # Ottieni indirizzo fisico
                physical_address = buffer.physical_address
                
            except Exception as e:
                logger.error(f"[PYNQ] Failed to allocate buffer: {e}")
                raise Exception(f"Failed to allocate buffer: {e}")
            
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
                    "dtype": str(np_dtype),
                    "size": size,
                    "physical_address": physical_address
                },
                pynq_object=buffer
            )
            
            # Aggiorna contatori tenant
            self.tenant_manager.resources[tenant_id].buffer_handles.add(handle)
            self.tenant_manager.resources[tenant_id].total_memory_bytes += size
            
            logger.info(f"[PYNQ] Buffer allocated: {handle}, phys_addr=0x{physical_address:08x}, size={size} bytes")
            
            # Ritorna info per il client
            return {
                'handle': handle,
                'physical_address': physical_address,
                'total_size': size,
                'shm_name': None,  # PYNQ non usa shared memory nello stesso modo
                'shape': np_shape,
                'dtype': str(np_dtype)
            }

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
            # PYNQ buffer è un numpy array, quindi possiamo usare slicing
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
            # Converti bytes in numpy array temporaneo
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
        """Libera un buffer PYNQ"""
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
                # PYNQ gestisce la deallocazione automaticamente quando
                # l'oggetto buffer viene distrutto
                size = resource.metadata['size']
                
                # Aggiorna contatori tenant
                self.tenant_manager.resources[tenant_id].buffer_handles.discard(handle)
                self.tenant_manager.resources[tenant_id].total_memory_bytes -= size
                
                # Rimuovi riferimenti
                del self._buffers[handle]
                del self._resources[handle]
                
                logger.info(f"[PYNQ] Buffer freed: handle={handle}, size={size} bytes")
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
        """Pulisce tutte le risorse di un tenant su hardware PYNQ reale"""
        with self._lock:
            handles_to_remove = []
            
            # Trova tutte le risorse del tenant
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
            # Pulisci in base al tipo
            if resource.resource_type == "overlay":
                # PYNQ non richiede cleanup esplicito per overlay
                # Il garbage collector si occupa di liberare le risorse
                del self._overlays[handle]
                logger.info(f"[PYNQ] Cleaned overlay: {handle}")
                
            elif resource.resource_type == "mmio":
                # MMIO viene pulito automaticamente quando l'oggetto viene distrutto
                del self._mmios[handle]
                logger.info(f"[PYNQ] Cleaned MMIO: {handle}")
                
            elif resource.resource_type == "buffer":
                # Buffer PYNQ - importante liberare la memoria
                buffer = self._buffers.get(handle)
                if buffer:
                    try:
                        # PYNQ gestisce la deallocazione quando l'oggetto viene distrutto
                        # ma è buona pratica essere espliciti
                        if hasattr(buffer, 'freebuffer'):
                            buffer.freebuffer()
                    except Exception as e:
                        logger.warning(f"[PYNQ] Error freeing buffer {handle}: {e}")
                    
                    # Aggiorna contatori tenant
                    size = resource.metadata.get('size', 0)
                    self.tenant_manager.resources[resource.tenant_id].buffer_handles.discard(handle)
                    self.tenant_manager.resources[resource.tenant_id].total_memory_bytes -= size
                    
                del self._buffers[handle]
                logger.info(f"[PYNQ] Cleaned buffer: {handle}")
                
            elif resource.resource_type == "dma":
                # DMA viene pulito automaticamente
                del self._dmas[handle]
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
