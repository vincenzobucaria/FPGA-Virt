# hypervisor/pr_zone_manager.py
import os
import re
import threading
import logging
from typing import Dict, Set, Optional, List, Tuple
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

@dataclass
class PRZoneAllocation:
    """Rappresenta un'allocazione di una zona PR"""
    zone_id: int
    tenant_id: str
    bitstream_path: str
    overlay_handle: str
    allocated_at: float

class PRZoneManager:
    """Gestisce l'allocazione delle zone parzialmente riconfigurabili"""
    
    def __init__(self, num_pr_zones: int = 2):
        self.num_pr_zones = num_pr_zones
        self._allocations: Dict[int, PRZoneAllocation] = {}  # zone_id -> allocation
        self._tenant_zones: Dict[str, Set[int]] = {}  # tenant_id -> set of zone_ids
        self._lock = threading.RLock()
        
        logger.info(f"Initialized PRZoneManager with {num_pr_zones} PR zones")
    
    def parse_bitstream_name(self, bitstream_path: str) -> Tuple[Optional[int], str]:
        """
        Estrae zona PR e nome base dal path del bitstream.
        
        Args:
            bitstream_path: Path del bitstream (es. "PR_0_sum.bit" o "/path/to/PR_1_conv.bit")
            
        Returns:
            Tuple di (pr_zone_number, base_name) o (None, original_path) se non matcha
        """
        filename = os.path.basename(bitstream_path)
        
        # Pattern: PR_<number>_<name>.bit
        pattern = r'^PR_(\d+)_(.+)\.bit$'
        match = re.match(pattern, filename)
        
        if match:
            zone_id = int(match.group(1))
            base_name = match.group(2)
            return zone_id, base_name
        
        # Se non matcha il pattern, ritorna None per zone_id
        return None, filename
    
    def find_bitstream_for_zone(self, zone_id: int, base_name: str, 
                                bitstream_dir: str, allowed_bitstreams: Set[str]) -> Optional[str]:
        """
        Trova il bitstream per una specifica zona PR.
        
        Args:
            zone_id: ID della zona PR
            base_name: Nome base del bitstream (es. "sum", "conv")
            bitstream_dir: Directory dei bitstream
            allowed_bitstreams: Set di bitstream permessi per il tenant
            
        Returns:
            Path completo del bitstream se trovato, None altrimenti
        """
        # Costruisci il nome atteso
        expected_filename = f"PR_{zone_id}_{base_name}.bit"
        
        # Verifica se è nei bitstream permessi
        if expected_filename not in allowed_bitstreams:
            logger.warning(f"Bitstream {expected_filename} not in allowed list")
            logger.warning(f"Allowed bitstreams: {allowed_bitstreams}")
            return None
        
        # Costruisci path completo
        full_path = os.path.join(bitstream_dir, expected_filename)
        logger.warning(f"Searching for bitstream ", full_path)
        # Verifica che il file esista
        if os.path.exists(full_path):
            return full_path
        
        logger.warning(f"Bitstream file not found: {full_path}")
        return None
    
    def get_available_zones(self) -> List[int]:
        """Ritorna lista delle zone PR disponibili"""
        with self._lock:
            available = []
            for zone_id in range(self.num_pr_zones):
                if zone_id not in self._allocations:
                    available.append(zone_id)
            return available
    
    def is_zone_available(self, zone_id: int) -> bool:
        """Controlla se una specifica zona è disponibile"""
        with self._lock:
            return zone_id not in self._allocations
    
    def allocate_zone(self, tenant_id: str, zone_id: int, 
                     bitstream_path: str, overlay_handle: str) -> bool:
        """
        Alloca una specifica zona PR a un tenant.
        
        Returns:
            True se allocata con successo, False se già occupata
        """
        import time
        
        with self._lock:
            if zone_id in self._allocations:
                logger.warning(f"Zone {zone_id} already allocated")
                return False
            
            # Crea allocazione
            allocation = PRZoneAllocation(
                zone_id=zone_id,
                tenant_id=tenant_id,
                bitstream_path=bitstream_path,
                overlay_handle=overlay_handle,
                allocated_at=time.time()
            )
            
            # Registra allocazione
            self._allocations[zone_id] = allocation
            
            # Aggiorna set zone del tenant
            if tenant_id not in self._tenant_zones:
                self._tenant_zones[tenant_id] = set()
            self._tenant_zones[tenant_id].add(zone_id)
            
            logger.info(f"Allocated PR zone {zone_id} to tenant {tenant_id} "
                       f"with bitstream {os.path.basename(bitstream_path)}")
            return True
    
    def release_zone(self, zone_id: int) -> Optional[str]:
        """
        Rilascia una zona PR.
        
        Returns:
            tenant_id del tenant che aveva la zona, None se non era allocata
        """
        with self._lock:
            if zone_id not in self._allocations:
                return None
            
            allocation = self._allocations[zone_id]
            tenant_id = allocation.tenant_id
            
            # Rimuovi allocazione
            del self._allocations[zone_id]
            
            # Aggiorna set zone del tenant
            if tenant_id in self._tenant_zones:
                self._tenant_zones[tenant_id].discard(zone_id)
                if not self._tenant_zones[tenant_id]:
                    del self._tenant_zones[tenant_id]
            
            logger.info(f"Released PR zone {zone_id} from tenant {tenant_id}")
            return tenant_id
    
    def release_zone_by_handle(self, overlay_handle: str) -> Optional[int]:
        """
        Rilascia una zona PR dato l'overlay handle.
        
        Returns:
            zone_id rilasciata, None se handle non trovato
        """
        with self._lock:
            for zone_id, allocation in self._allocations.items():
                if allocation.overlay_handle == overlay_handle:
                    self.release_zone(zone_id)
                    return zone_id
            return None
    
    def get_tenant_zones(self, tenant_id: str) -> Set[int]:
        """Ritorna le zone allocate a un tenant"""
        with self._lock:
            return self._tenant_zones.get(tenant_id, set()).copy()
    
    def release_all_tenant_zones(self, tenant_id: str) -> List[int]:
        """
        Rilascia tutte le zone di un tenant.
        
        Returns:
            Lista delle zone rilasciate
        """
        with self._lock:
            zones = self.get_tenant_zones(tenant_id)
            released = []
            
            for zone_id in zones:
                if self.release_zone(zone_id):
                    released.append(zone_id)
            
            return released
    
    def find_best_zone_for_bitstream(self, requested_bitstream: str, 
                                    tenant_id: str,
                                    bitstream_dir: str,
                                    allowed_bitstreams: Set[str]) -> Optional[Tuple[int, str]]:
        """
        Trova la migliore zona PR disponibile per un bitstream richiesto.
        
        Args:
            requested_bitstream: Bitstream richiesto (può essere con o senza PR_N_)
            tenant_id: ID del tenant
            bitstream_dir: Directory dei bitstream
            allowed_bitstreams: Bitstream permessi per il tenant
            
        Returns:
            Tuple di (zone_id, actual_bitstream_path) se trovato, None altrimenti
        """
        with self._lock:
            # Prima, estrai info dal bitstream richiesto
            requested_zone, base_name = self.parse_bitstream_name(requested_bitstream)
            
            # Se il bitstream richiesto specifica già una zona
            if requested_zone is not None:
                if self.is_zone_available(requested_zone):
                    # La zona richiesta è disponibile
                    full_path = os.path.join(bitstream_dir, requested_bitstream)
                    if os.path.exists(full_path):
                        return requested_zone, full_path
                else:
                    logger.info(f"Requested zone {requested_zone} is not available")
                    return None
            
            # Se non specifica zona o la zona richiesta non è disponibile,
            # cerca una zona libera
            available_zones = self.get_available_zones()
            if not available_zones:
                logger.warning("No PR zones available")
                return None
            logger.warning("Available zones: ", available_zones)
            # Prova ogni zona disponibile
            for zone_id in available_zones:
                bitstream_path = self.find_bitstream_for_zone(
                    zone_id, base_name, bitstream_dir, allowed_bitstreams
                )
                if bitstream_path:
                    return zone_id, bitstream_path
            
            logger.warning(f"No suitable bitstream found for {base_name} in any available zone")
            return None
    
    def get_allocation_info(self) -> Dict:
        """Ritorna informazioni sulle allocazioni correnti"""
        with self._lock:
            info = {
                'total_zones': self.num_pr_zones,
                'allocated_zones': len(self._allocations),
                'available_zones': self.num_pr_zones - len(self._allocations),
                'allocations': {}
            }
            
            for zone_id, allocation in self._allocations.items():
                info['allocations'][f'PR_{zone_id}'] = {
                    'tenant_id': allocation.tenant_id,
                    'bitstream': os.path.basename(allocation.bitstream_path),
                    'overlay_handle': allocation.overlay_handle,
                    'allocated_at': allocation.allocated_at
                }
            
            return info