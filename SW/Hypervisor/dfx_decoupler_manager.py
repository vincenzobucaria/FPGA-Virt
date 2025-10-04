# hypervisor/dfx_decoupler_gpio.py
import time
import logging
from typing import Dict, Optional
from dataclasses import dataclass
from pynq import GPIO
from pynq import Bitstream

logger = logging.getLogger(__name__)

@dataclass
class DFXDecouplerConfig:
    """Configurazione per un DFX decoupler GPIO"""
    zone_id: int
    gpio_pin: int  # Pin GPIO da usare
    decouple_value: int = 1  # Valore per isolare (1)
    couple_value: int = 0    # Valore per connettere (0)

class DFXDecouplerManager:
    """Gestisce i DFX decouplers tramite GPIO per la riconfigurazione parziale"""
    
    def __init__(self, static_overlay=None):
        """
        Args:
            static_overlay: Non più necessario con GPIO diretti
        """
        self.decouplers: Dict[int, GPIO] = {}
        self.decoupler_configs: Dict[int, DFXDecouplerConfig] = {}
        self._decoupler_states: Dict[int, bool] = {}  # True = decoupled, False = coupled
        
        logger.info("[DFX] Initialized DFX Decoupler Manager with GPIO")
    
    def register_decoupler(self, zone_id: int, gpio_pin: int = None, **kwargs):
        """
        Registra un DFX decoupler GPIO per una PR zone.
        
        Args:
            zone_id: ID della PR zone
            gpio_pin: Pin GPIO da usare (se None, usa zone_id come pin number)
            **kwargs: Parametri opzionali per DFXDecouplerConfig
        """
        # Se gpio_pin non specificato, usa zone_id come default
        if gpio_pin is None:
            gpio_pin = zone_id
        
        try:
            # Crea configurazione
            config = DFXDecouplerConfig(
                zone_id=zone_id,
                gpio_pin=gpio_pin,
                **kwargs
            )
            
            # Crea oggetto GPIO
            gpio_obj = GPIO(GPIO.get_gpio_pin(gpio_pin), 'out')
            
            # Salva riferimenti
            self.decouplers[zone_id] = gpio_obj
            self.decoupler_configs[zone_id] = config
            self._decoupler_states[zone_id] = False  # Inizialmente coupled
            
            # Assicura che sia coupled all'inizio
            gpio_obj.write(config.couple_value)
            
            logger.info(f"[DFX] Registered GPIO decoupler for PR zone {zone_id} on pin {gpio_pin}")
            
        except Exception as e:
            logger.error(f"[DFX] Failed to register decoupler for zone {zone_id}: {e}")
            raise
    
    def decouple_zone(self, zone_id: int):
        """
        Disaccoppia (isola) una PR zone prima della riconfigurazione.
        
        Args:
            zone_id: ID della PR zone da isolare
        """
        if zone_id not in self.decouplers:
            raise ValueError(f"No decoupler registered for PR zone {zone_id}")
        
        config = self.decoupler_configs[zone_id]
        gpio_obj = self.decouplers[zone_id]
        
        logger.info(f"[DFX] Decoupling PR zone {zone_id} using GPIO pin {config.gpio_pin}")
        
        # Decouple (isola) la PR region
        gpio_obj.write(config.decouple_value)  # Scrivi 1
        
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
        
        config = self.decoupler_configs[zone_id]
        gpio_obj = self.decouplers[zone_id]
        
        logger.info(f"[DFX] Coupling PR zone {zone_id} using GPIO pin {config.gpio_pin}")
        
        # Couple (connette) la PR region
        gpio_obj.write(config.couple_value)  # Scrivi 0
        
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
                # Forza il valore per sicurezza
                config = self.decoupler_configs[zone_id]
                gpio_obj = self.decouplers[zone_id]
                gpio_obj.write(config.couple_value)
    
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
            logger.info(f"[DFX] === PARTIAL RECONFIGURATION SEQUENCE FOR PR {zone_id} ===")
            
            # 1. Decouple la PR zone
            logger.info(f"[DFX] Step 1: Decoupling PR region {zone_id}...")
            self.decouple_zone(zone_id)
            
            # 2. Carica il bitstream parziale
            logger.info(f"[DFX] Step 2: Loading bitstream: {bitstream_path}")
            
            partial_bitstream = Bitstream(bitstream_path, None, True)
            
            start_time = time.time()
            partial_bitstream.download()
            end_time = time.time()
            
            logger.info(f"[DFX] Bitstream loaded in {end_time - start_time:.3f} seconds")
            
            # Pausa per assicurarsi che la riconfigurazione sia completa
            time.sleep(0.2)
            
            # 3. Re-couple la PR zone
            logger.info(f"[DFX] Step 3: Recoupling PR region {zone_id}...")
            self.couple_zone(zone_id)
            
            logger.info(f"[DFX] === RECONFIGURATION COMPLETED SUCCESSFULLY ===")
            return True
            
        except Exception as e:
            logger.error(f"[DFX] Error during partial reconfiguration: {e}")
            # In caso di errore, prova a re-couple comunque
            try:
                self.couple_zone(zone_id)
            except:
                pass
            return False
    
    def get_status(self) -> Dict:
        """Ottieni stato di tutti i decouplers"""
        status = {}
        for zone_id, gpio_obj in self.decouplers.items():
            config = self.decoupler_configs[zone_id]
            try:
                current_value = gpio_obj.read()
                status[f'PR_{zone_id}'] = {
                    'gpio_pin': config.gpio_pin,
                    'current_value': current_value,
                    'decoupled': self.is_decoupled(zone_id),
                    'state': 'DECOUPLED' if current_value == config.decouple_value else 'COUPLED'
                }
            except Exception as e:
                status[f'PR_{zone_id}'] = {
                    'error': str(e)
                }
        return status