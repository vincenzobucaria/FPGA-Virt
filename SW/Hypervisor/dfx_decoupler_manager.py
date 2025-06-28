# hypervisor/dfx_decoupler_manager.py
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class DFXDecouplerConfig:
    """Configurazione per un DFX decoupler"""
    zone_id: int
    decoupler_name: str  # es. "axi_gpio_0"
    decouple_value: int = 1  # Valore per isolare (1)
    couple_value: int = 0    # Valore per connettere (0)

class DFXDecouplerManager:
    """Gestisce i DFX decouplers per la riconfigurazione parziale"""
    
    def __init__(self, static_overlay):
        """
        Args:
            static_overlay: Overlay PYNQ della shell statica (full.bit)
        """
        self.static_overlay = static_overlay
        self.decouplers: Dict[int, DFXDecouplerConfig] = {}
        self._decoupler_states: Dict[int, bool] = {}  # True = decoupled, False = coupled
        
        logger.info("[DFX] Initialized DFX Decoupler Manager")
    
    def register_decoupler(self, zone_id: int, decoupler_name: str, **kwargs):
        """
        Registra un DFX decoupler per una PR zone.
        
        Args:
            zone_id: ID della PR zone
            decoupler_name: Nome del decoupler nell'overlay (es. "axi_gpio_0")
            **kwargs: Parametri opzionali per DFXDecouplerConfig
        """
        # Verifica che il decoupler esista nell'overlay
        if not hasattr(self.static_overlay, decoupler_name):
            raise ValueError(f"Decoupler {decoupler_name} not found in static overlay")
        
        config = DFXDecouplerConfig(
            zone_id=zone_id,
            decoupler_name=decoupler_name,
            **kwargs
        )
        
        self.decouplers[zone_id] = config
        self._decoupler_states[zone_id] = False  # Inizialmente coupled
        
        # IMPORTANTE: Configura il tristate come output UNA SOLA VOLTA all'inizializzazione
        decoupler = getattr(self.static_overlay, config.decoupler_name)
        decoupler.register_map.GPIO_TRI.CH1_TRI = 0  # 0 = output, e resta sempre così
        
        logger.info(f"[DFX] Registered decoupler {decoupler_name} for PR zone {zone_id} - tristate set to OUTPUT")
    
    def decouple_zone(self, zone_id: int):
        """
        Disaccoppia (isola) una PR zone prima della riconfigurazione.
        
        Args:
            zone_id: ID della PR zone da isolare
        """
        if zone_id not in self.decouplers:
            raise ValueError(f"No decoupler registered for PR zone {zone_id}")
        
        config = self.decouplers[zone_id]
        
        # Ottieni il decoupler dall'overlay
        decoupler = getattr(self.static_overlay, config.decoupler_name)
        if not decoupler:
            raise RuntimeError(f"Cannot access decoupler {config.decoupler_name}")
        
        logger.info(f"[DFX] Decoupling PR zone {zone_id} using {config.decoupler_name}")
        
        # Decouple (isola) la PR region usando CH1
        decoupler.register_map.GPIO_DATA.CH1_DATA = config.decouple_value  # 1
        
        # Piccola pausa per assicurarsi che il decoupling sia completo
        time.sleep(0.1)
        
        self._decoupler_states[zone_id] = True
        logger.info(f"[DFX] PR zone {zone_id} DECOUPLED (isolated)")
    
    def couple_zone(self, zone_id: int):
        """
        Riaccoppia (connette) una PR zone dopo la riconfigurazione.
        
        Args:
            zone_id: ID della PR zone da riconnettere
        """
        if zone_id not in self.decouplers:
            raise ValueError(f"No decoupler registered for PR zone {zone_id}")
        
        config = self.decouplers[zone_id]
        
        # Ottieni il decoupler dall'overlay
        decoupler = getattr(self.static_overlay, config.decoupler_name)
        if not decoupler:
            raise RuntimeError(f"Cannot access decoupler {config.decoupler_name}")
        
        logger.info(f"[DFX] Coupling PR zone {zone_id} using {config.decoupler_name}")
        
        # Couple (connette) la PR region usando CH1
        decoupler.register_map.GPIO_DATA.CH1_DATA = config.couple_value  # 0
        
        # Piccola pausa per stabilizzazione
        time.sleep(0.1)
        
        self._decoupler_states[zone_id] = False
        logger.info(f"[DFX] PR zone {zone_id} COUPLED (connected)")
    
    def is_decoupled(self, zone_id: int) -> bool:
        """Verifica se una PR zone è attualmente disaccoppiata"""
        return self._decoupler_states.get(zone_id, False)
    
    def ensure_all_coupled(self):
        """Assicura che tutte le PR zones siano accoppiate (utile all'avvio)"""
        for zone_id in self.decouplers:
            if self.is_decoupled(zone_id):
                self.couple_zone(zone_id)
            else:
                # Anche se lo stato dice che è coupled, forza il valore per sicurezza
                config = self.decouplers[zone_id]
                decoupler = getattr(self.static_overlay, config.decoupler_name)
                decoupler.register_map.GPIO_DATA.CH1_DATA = config.couple_value  # 0
    
    def reconfigure_pr_zone(self, zone_id: int, bitstream_path: str) -> bool:
        """
        Esegue la sequenza completa di riconfigurazione parziale.
        
        Args:
            zone_id: ID della PR zone
            bitstream_path: Path del bitstream parziale
            
        Returns:
            True se successo, False altrimenti
        """
        try:
            # 1. Decouple la PR zone
            self.decouple_zone(zone_id)
            
            # 2. Carica il bitstream parziale
            logger.info(f"[DFX] Loading partial bitstream: {bitstream_path}")
            from pynq import Bitstream
            
            partial_bitstream = Bitstream(bitstream_path, None, True)
            partial_bitstream.download()
            
            # Pausa per assicurarsi che la riconfigurazione sia completa
            time.sleep(0.2)
            
            # 3. Re-couple la PR zone
            self.couple_zone(zone_id)
            
            logger.info(f"[DFX] Partial reconfiguration of zone {zone_id} completed successfully")
            return True
            
        except Exception as e:
            logger.error(f"[DFX] Error during partial reconfiguration: {e}")
            # In caso di errore, prova a re-couple comunque
            try:
                self.couple_zone(zone_id)
            except:
                pass
            return False