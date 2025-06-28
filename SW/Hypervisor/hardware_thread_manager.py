# hypervisor/hardware_thread_manager.py - VERSIONE COMPLETA CON FIX E DEBUG
import threading
import queue
import time
import logging
from typing import Dict, Any, Callable, Optional, Tuple
from dataclasses import dataclass
import traceback
import os

logger = logging.getLogger(__name__)

def log_thread_info(location):
    """Helper per loggare info dettagliate del thread"""
    current = threading.current_thread()
    tid = threading.get_ident()
    all_threads = threading.enumerate()
    
    logger.info(f"[THREAD_DEBUG] {location}:")
    logger.info(f"  Current thread: {current.name} (ID: {tid})")
    logger.info(f"  Total threads: {len(all_threads)}")
    for t in all_threads:
        logger.info(f"    - {t.name}: {'alive' if t.is_alive() else 'dead'}")

@dataclass
class HardwareOperation:
    """Rappresenta un'operazione hardware da eseguire"""
    operation_id: str
    function: Callable
    args: tuple
    kwargs: dict
    result_queue: queue.Queue
    tenant_id: str

class HardwareThreadManager:
    """
    Manager che esegue TUTTE le operazioni hardware in un singolo thread dedicato.
    Versione con fix completi e debugging dettagliato.
    """
    
    def __init__(self):
        self._operation_queue = queue.Queue()
        self._hardware_thread = None
        self._running = False
        self._initialized = False
        
        # Riferimenti agli oggetti hardware (creati nel thread hardware)
        self._static_overlay = None
        self._dfx_manager = None
        self._pr_zone_manager = None
        
        # Cache per evitare ricreazioni
        self._mmio_objects = {}
        self._buffer_objects = {}
        
        # Salva reference alle classi PYNQ
        self._Overlay = None
        self._Bitstream = None
        self._MMIO = None
        self._allocate = None
        
        logger.info("[HW_THREAD] Hardware Thread Manager initialized")
        log_thread_info("HardwareThreadManager.__init__")
    
    def start(self):
        """Avvia il thread hardware dedicato"""
        log_thread_info("HardwareThreadManager.start")
        
        if self._hardware_thread and self._hardware_thread.is_alive():
            logger.warning("[HW_THREAD] Hardware thread already running")
            return
        
        self._running = True
        self._hardware_thread = threading.Thread(
            target=self._hardware_thread_loop,
            name="HardwareThread",
            daemon=False
        )
        self._hardware_thread.start()
        
        # Attendi inizializzazione
        init_timeout = 30
        start_time = time.time()
        while not self._initialized and time.time() - start_time < init_timeout:
            time.sleep(0.1)
        
        if not self._initialized:
            raise RuntimeError("Hardware thread initialization timeout")
        
        logger.info("[HW_THREAD] Hardware thread started successfully")
    
    def stop(self):
        """Ferma il thread hardware"""
        log_thread_info("HardwareThreadManager.stop")
        logger.info("[HW_THREAD] Stopping hardware thread...")
        self._running = False
        
        # Invia comando di stop
        self._operation_queue.put(None)
        
        # Attendi terminazione
        if self._hardware_thread:
            self._hardware_thread.join(timeout=10)
            if self._hardware_thread.is_alive():
                logger.error("[HW_THREAD] Hardware thread failed to stop cleanly")
        
        logger.info("[HW_THREAD] Hardware thread stopped")
    
    def _hardware_thread_loop(self):
        """Loop principale del thread hardware - TUTTE le operazioni PYNQ avvengono qui"""
        log_thread_info("HardwareThread._hardware_thread_loop START")
        logger.info(f"[HW_THREAD] Hardware thread started (TID: {threading.get_ident()})")
        
        try:
            # IMPORTANTE: Setup asyncio PRIMA di importare PYNQ
            import asyncio
            try:
                # Crea un nuovo event loop per questo thread
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                logger.info("[HW_THREAD] Created asyncio event loop")
            except Exception as e:
                logger.warning(f"[HW_THREAD] Could not create event loop: {e}")
            
            # IMPORTANTE: Importa PYNQ solo in questo thread!
            log_thread_info("HardwareThread.before_pynq_import")
            logger.info("[HW_THREAD] Importing PYNQ modules...")
            
            from pynq import Overlay, Bitstream, allocate, MMIO
            
            # Salva le classi per uso futuro
            self._Overlay = Overlay
            self._Bitstream = Bitstream
            self._MMIO = MMIO
            self._allocate = allocate
            
            logger.info("[HW_THREAD] PYNQ modules imported successfully")
            log_thread_info("HardwareThread.after_pynq_import")
            
            # Inizializza hardware nel thread dedicato
            logger.info("[HW_THREAD] Initializing hardware...")
            self._initialize_hardware_in_thread()
            
            self._initialized = True
            logger.info("[HW_THREAD] Hardware initialization complete")
            
            # Loop principale
            while self._running:
                try:
                    # Attendi operazione (timeout per permettere stop pulito)
                    operation = self._operation_queue.get(timeout=1.0)
                    
                    if operation is None:  # Segnale di stop
                        break
                    
                    # Log prima di eseguire
                    log_thread_info(f"HardwareThread.before_operation_{operation.operation_id}")
                    
                    # Esegui operazione
                    self._execute_operation(operation)
                    
                    # Log dopo esecuzione
                    log_thread_info(f"HardwareThread.after_operation_{operation.operation_id}")
                    
                except queue.Empty:
                    continue
                except Exception as e:
                    logger.error(f"[HW_THREAD] Error in operation loop: {e}")
                    traceback.print_exc()
        
        except Exception as e:
            logger.error(f"[HW_THREAD] Fatal error in hardware thread: {e}")
            traceback.print_exc()
        finally:
            # Cleanup
            self._cleanup_hardware()
            logger.info("[HW_THREAD] Hardware thread terminated")
            log_thread_info("HardwareThread._hardware_thread_loop END")
    
    def _initialize_hardware_in_thread(self):
        """Inizializza tutti gli oggetti hardware nel thread dedicato"""
        log_thread_info("HardwareThread._initialize_hardware_in_thread START")
        
        # Verifica che siamo nel thread giusto
        current = threading.current_thread()
        if current.name != "HardwareThread":
            raise RuntimeError(f"Hardware initialization must happen in HardwareThread! Current: {current.name}")
        
        # Carica overlay statico
        logger.info("[HW_THREAD] Loading static overlay...")
        self._static_overlay = self._Overlay("/home/xilinx/bitstreams/full.bit")
        logger.info("[HW_THREAD] Static overlay loaded")
        
        # Inizializza DFX manager (importato localmente)
        log_thread_info("HardwareThread.before_dfx_import")
        from dfx_decoupler_manager import DFXDecouplerManager
        self._dfx_manager = DFXDecouplerManager(self._static_overlay)
        
        # Inizializza PR zone manager
        from pr_zone_manager import PRZoneManager
        self._pr_zone_manager = PRZoneManager(num_pr_zones=2)
        
        # Registra decouplers
        self._dfx_manager.register_decoupler(0, "axi_gpio_0")
        self._dfx_manager.register_decoupler(1, "axi_gpio_1")
        
        # Assicura che tutte le zone siano accoppiate
        self._dfx_manager.ensure_all_coupled()
        
        log_thread_info("HardwareThread._initialize_hardware_in_thread END")
    
    def _execute_operation(self, operation: HardwareOperation):
        """Esegue un'operazione hardware nel thread dedicato"""
        result = None
        error = None
        
        try:
            logger.debug(f"[HW_THREAD] Executing operation: {operation.operation_id} "
                        f"for tenant {operation.tenant_id}")
            
            # Verifica thread
            current = threading.current_thread()
            if current.name != "HardwareThread":
                raise RuntimeError(f"Operation executing in wrong thread: {current.name}")
            
            # Esegui la funzione
            result = operation.function(*operation.args, **operation.kwargs)
            
            logger.debug(f"[HW_THREAD] Operation {operation.operation_id} completed successfully")
            
        except Exception as e:
            error = e
            logger.error(f"[HW_THREAD] Operation {operation.operation_id} failed: {e}")
            traceback.print_exc()
        
        # Invia risultato
        operation.result_queue.put((result, error))
    
    def _cleanup_hardware(self):
        """Pulisce risorse hardware"""
        log_thread_info("HardwareThread._cleanup_hardware")
        logger.info("[HW_THREAD] Cleaning up hardware resources...")
        
        # Cleanup MMIO
        for handle, mmio in self._mmio_objects.items():
            try:
                del mmio
            except:
                pass
        self._mmio_objects.clear()
        
        # Cleanup buffers
        for handle, buffer in self._buffer_objects.items():
            try:
                if hasattr(buffer, 'freebuffer'):
                    buffer.freebuffer()
            except:
                pass
        self._buffer_objects.clear()
        
        # Cleanup overlay
        if self._static_overlay:
            try:
                del self._static_overlay
            except:
                pass
    
    def execute_hardware_operation(self, tenant_id: str, operation_name: str, 
                                   function: Callable, *args, **kwargs) -> Any:
        """
        Esegue un'operazione hardware nel thread dedicato e attende il risultato.
        """
        log_thread_info(f"execute_hardware_operation.{operation_name}")
        
        # Verifica se siamo già nel thread hardware
        current = threading.current_thread()
        if current.name == "HardwareThread":
            logger.warning(f"[HW_THREAD] Already in HardwareThread! Direct execution of {operation_name}")
            # Esegui direttamente
            return function(*args, **kwargs)
        
        if not self._running:
            raise RuntimeError("Hardware thread not running")
        
        # Crea coda per risultato
        result_queue = queue.Queue()
        
        # Crea operazione
        operation = HardwareOperation(
            operation_id=f"{operation_name}_{int(time.time()*1000)}",
            function=function,
            args=args,
            kwargs=kwargs,
            result_queue=result_queue,
            tenant_id=tenant_id
        )
        
        logger.info(f"[HW_THREAD] Queueing operation {operation.operation_id}")
        
        # Invia al thread hardware
        self._operation_queue.put(operation)
        
        # Attendi risultato (con timeout)
        try:
            result, error = result_queue.get(timeout=30)
            
            if error:
                raise error
            
            return result
            
        except queue.Empty:
            raise TimeoutError(f"Operation {operation_name} timed out")
    
    # Metodi helper per operazioni comuni
    def load_pr_bitstream(self, tenant_id: str, zone_id: int, bitstream_path: str) -> bool:
        """Carica un bitstream PR nel thread hardware"""
        log_thread_info(f"load_pr_bitstream zone_{zone_id}")
        
        def _load():
            # Verifica thread
            current = threading.current_thread()
            logger.info(f"[HW_THREAD] _load executing in thread: {current.name} (ID: {threading.get_ident()})")
            
            if current.name != "HardwareThread":
                raise RuntimeError(f"Not in HardwareThread! Current: {current.name}")
            
            # Verifica che DFX manager esista
            if not self._dfx_manager:
                raise RuntimeError("DFX Manager not initialized!")
            
            # Chiama reconfigure nel thread hardware
            return self._dfx_manager.reconfigure_pr_zone(zone_id, bitstream_path)
        
        return self.execute_hardware_operation(
            tenant_id, f"load_pr_zone_{zone_id}", _load
        )
    
    def create_mmio(self, tenant_id: str, base_address: int, length: int) -> str:
        """Crea MMIO nel thread hardware"""
        log_thread_info("create_mmio")
        
        def _create():
            # Verifica thread
            current = threading.current_thread()
            if current.name != "HardwareThread":
                raise RuntimeError(f"Not in HardwareThread! Current: {current.name}")
            
            # Usa la classe MMIO salvata
            if not self._MMIO:
                raise RuntimeError("MMIO class not available!")
            
            handle = f"mmio_{base_address:08x}_{int(time.time()*1000)}"
            mmio = self._MMIO(base_address, length)
            self._mmio_objects[handle] = mmio
            logger.info(f"[HW_THREAD] Created MMIO {handle} at 0x{base_address:08x}")
            return handle
        
        return self.execute_hardware_operation(
            tenant_id, "create_mmio", _create
        )
    
    def mmio_read(self, tenant_id: str, handle: str, offset: int, length: int = 4) -> int:
        """Legge da MMIO nel thread hardware"""
        def _read():
            mmio = self._mmio_objects.get(handle)
            if mmio is None:
                raise ValueError(f"MMIO handle {handle} not found")
            return mmio.read(offset, length)
        
        return self.execute_hardware_operation(
            tenant_id, "mmio_read", _read
        )
    
    def mmio_write(self, tenant_id: str, handle: str, offset: int, value: int):
        """Scrive su MMIO nel thread hardware"""
        def _write():
            mmio = self._mmio_objects.get(handle)
            if mmio is None:
                raise ValueError(f"MMIO handle {handle} not found")
            mmio.write(offset, value)
        
        return self.execute_hardware_operation(
            tenant_id, "mmio_write", _write
        )
    
    def allocate_buffer(self, tenant_id: str, shape, dtype='uint8') -> Tuple[str, int]:
        """Alloca buffer nel thread hardware"""
        log_thread_info("allocate_buffer")
        
        def _allocate():
            # Verifica thread
            current = threading.current_thread()
            if current.name != "HardwareThread":
                raise RuntimeError(f"Not in HardwareThread! Current: {current.name}")
            
            # Usa allocate salvato
            if not self._allocate:
                raise RuntimeError("allocate function not available!")
            
            import numpy as np
            
            np_shape = tuple(shape) if isinstance(shape, (list, tuple)) else (shape,)
            np_dtype = np.dtype(dtype)
            
            buffer = self._allocate(shape=np_shape, dtype=np_dtype)
            handle = f"buffer_{int(time.time()*1000)}"
            self._buffer_objects[handle] = buffer
            
            logger.info(f"[HW_THREAD] Allocated buffer {handle}, phys_addr=0x{buffer.physical_address:08x}")
            return handle, buffer.physical_address
        
        return self.execute_hardware_operation(
            tenant_id, "allocate_buffer", _allocate
        )
    
    def read_buffer(self, tenant_id: str, handle: str, offset: int, length: int) -> bytes:
        """Legge dati da buffer nel thread hardware"""
        def _read():
            buffer = self._buffer_objects.get(handle)
            if buffer is None:
                raise ValueError(f"Buffer handle {handle} not found")
            
            # Verifica limiti
            buffer_size = buffer.nbytes
            if offset < 0 or offset >= buffer_size:
                raise ValueError(f"Offset {offset} out of bounds [0, {buffer_size})")
            
            if offset + length > buffer_size:
                raise ValueError(f"Read would exceed buffer bounds")
            
            # Leggi dati
            data_bytes = buffer.tobytes()[offset:offset+length]
            return data_bytes
        
        return self.execute_hardware_operation(
            tenant_id, "read_buffer", _read
        )
    
    def write_buffer(self, tenant_id: str, handle: str, data: bytes, offset: int):
        """Scrive dati in buffer nel thread hardware"""
        def _write():
            buffer = self._buffer_objects.get(handle)
            if buffer is None:
                raise ValueError(f"Buffer handle {handle} not found")
            
            # Verifica limiti
            buffer_size = buffer.nbytes
            data_length = len(data)
            
            if offset < 0 or offset >= buffer_size:
                raise ValueError(f"Offset {offset} out of bounds [0, {buffer_size})")
            
            if offset + data_length > buffer_size:
                raise ValueError(f"Write would exceed buffer bounds")
            
            # Scrivi dati
            import numpy as np
            temp_array = np.frombuffer(data, dtype=buffer.dtype)
            start_idx = offset // buffer.itemsize
            end_idx = start_idx + len(temp_array)
            buffer.flat[start_idx:end_idx] = temp_array
            buffer.flush()
        
        return self.execute_hardware_operation(
            tenant_id, "write_buffer", _write
        )
    
    def free_buffer(self, tenant_id: str, handle: str):
        """Libera un buffer nel thread hardware"""
        def _free():
            buffer = self._buffer_objects.get(handle)
            if buffer is not None:
                if hasattr(buffer, 'freebuffer'):
                    buffer.freebuffer()
                del self._buffer_objects[handle]
        
        return self.execute_hardware_operation(
            tenant_id, "free_buffer", _free
        )
    
    def destroy_mmio(self, tenant_id: str, handle: str):
        """Distrugge un MMIO nel thread hardware"""
        def _destroy():
            if handle in self._mmio_objects:
                del self._mmio_objects[handle]
        
        return self.execute_hardware_operation(
            tenant_id, "destroy_mmio", _destroy
        )

# Singleton globale
_hardware_thread_manager = None
_manager_lock = threading.Lock()

def get_hardware_thread_manager() -> HardwareThreadManager:
    """Ottieni l'istanza singleton del manager hardware"""
    global _hardware_thread_manager
    
    if _hardware_thread_manager is None:
        with _manager_lock:
            if _hardware_thread_manager is None:
                _hardware_thread_manager = HardwareThreadManager()
    
    return _hardware_thread_manager