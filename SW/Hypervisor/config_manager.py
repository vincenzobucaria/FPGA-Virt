# hypervisor/config_manager.py
import os
import yaml
import threading
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict, field
import logging

logger = logging.getLogger(__name__)

@dataclass
class TenantConfig:
    """Configurazione per un singolo tenant"""
    tenant_id: str
    uid: int
    gid: int
    api_key: str
    max_overlays: int = 2
    max_buffers: int = 10
    max_memory_mb: int = 256
    allowed_bitstreams: Set[str] = field(default_factory=set)
    allowed_address_ranges: List[tuple] = field(default_factory=list)  # Deprecato
    allowed_pr_zones: Set[int] = field(default_factory=set)  # Nuovo campo

@dataclass
class PRZoneConfig:
    """Configurazione per una PR zone"""
    zone_id: int
    name: str
    gpio_pin: int
    address_ranges: List[tuple] = field(default_factory=list)

class DynamicConfigManager:
    """Gestore dinamico della configurazione con supporto per PR zones"""
    
    def __init__(self, config_file: str = None):
        self.config_file = config_file
        self._lock = threading.RLock()
        self._config_watchers = []
        
        # Valori di default
        self.num_pr_zones = 2
        self.socket_dir = '/var/run/pynq'
        self.bitstream_dir = '/home/xilinx/bitstreams'
        self.static_bitstream = '/home/xilinx/bitstreams/full.bit'
        self.pr_zones = []
        self.tenants = {}
        
        # Carica config iniziale
        if config_file and os.path.exists(config_file):
            self._load_from_file()
        else:
            logger.warning(f"Config file not found: {config_file}, using defaults")
            self._load_default_config()
    
    def _load_from_file(self):
        """Carica configurazione da file YAML con supporto PR zones"""
        logger.info(f"Loading config from: {self.config_file}")
        
        try:
            with open(self.config_file, 'r') as f:
                data = yaml.safe_load(f)
            
            # Carica configurazione globale
            global_config = data.get('global', {})
            self.num_pr_zones = global_config.get('num_pr_zones', 2)
            self.socket_dir = global_config.get('socket_dir', '/var/run/pynq')
            self.bitstream_dir = global_config.get('bitstream_dir', '/home/xilinx/bitstreams')
            self.static_bitstream = global_config.get('static_bitstream', '/home/xilinx/bitstreams/full.bit')
            
            # Override da environment se disponibili
            self.socket_dir = os.environ.get('PYNQ_SOCKET_DIR', self.socket_dir)
            self.bitstream_dir = os.environ.get('PYNQ_BITSTREAM_DIR', self.bitstream_dir)
            
            # Carica configurazione PR zones
            self.pr_zones = []
            for zone_data in global_config.get('pr_zones', []):
                zone = PRZoneConfig(
                    zone_id=zone_data['zone_id'],
                    name=zone_data['name'],
                    gpio_pin=zone_data['gpio_pin'],
                    address_ranges=[tuple(r) for r in zone_data.get('address_ranges', [])]
                )
                self.pr_zones.append(zone)
                logger.info(f"Added PR zone: {zone.name} with decoupler GPIO pin: {zone.gpio_pin}")
            
            # Se non ci sono PR zones definite, crea default
            if not self.pr_zones and self.num_pr_zones > 0:
                self._create_default_pr_zones()
            
            # Carica tenants
            self.tenants = {}
            for tenant_data in data.get('tenants', []):
                tenant = self._load_tenant_from_dict(tenant_data)
                self.tenants[tenant.tenant_id] = tenant
                logger.info(f"Added tenant: {tenant.tenant_id}")
                
        except Exception as e:
            logger.error(f"Error loading config file: {e}")
            self._load_default_config()
    
    def _load_tenant_from_dict(self, tenant_data: dict) -> TenantConfig:
        """Crea TenantConfig da dizionario con supporto per PR zones"""
        # Gestisci il nuovo campo allowed_pr_zones
        allowed_pr_zones = tenant_data.get('allowed_pr_zones', None)
        if allowed_pr_zones is None:
            # Se non specificato, permetti tutte le zone
            allowed_pr_zones = list(range(self.num_pr_zones))
        
        tenant = TenantConfig(
            tenant_id=tenant_data['id'],
            uid=tenant_data['uid'],
            gid=tenant_data['gid'],
            api_key=tenant_data.get('api_key', ''),
            max_overlays=tenant_data.get('max_overlays', 2),
            max_buffers=tenant_data.get('max_buffers', 10),
            max_memory_mb=tenant_data.get('max_memory_mb', 256),
            allowed_bitstreams=set(tenant_data.get('allowed_bitstreams', [])),
            allowed_address_ranges=[],  # Deprecato
            allowed_pr_zones=set(allowed_pr_zones)
        )
        
        return tenant
    
    def _create_default_pr_zones(self):
        """Crea PR zones di default se non specificate"""
        base_address = 0xA0000000
        zone_offset = 0x100000  # 1MB tra zone
        
        for i in range(self.num_pr_zones):
            zone_base = base_address + (i * zone_offset)
            zone = PRZoneConfig(
                zone_id=i,
                name=f'PR_{i}',
                gpio_pin=f'axi_gpio_{i}',
                address_ranges=[
                    (zone_base, 0x10000),  # 64KB
                    (zone_base + 0x10000, 0x10000)  # Altri 64KB
                ]
            )
            self.pr_zones.append(zone)
            logger.info(f"Created default PR zone: {zone.name}")
    
    def _load_default_config(self):
        """Carica configurazione di default per testing"""
        logger.info("Loading default configuration")
        
        # Configurazione globale default
        self.num_pr_zones = 2
        self.socket_dir = os.environ.get('PYNQ_SOCKET_DIR', '/var/run/pynq')
        self.bitstream_dir = os.environ.get('PYNQ_BITSTREAM_DIR', '/home/xilinx/bitstreams')
        self.static_bitstream = '/home/xilinx/bitstreams/full.bit'
        
        # PR zones default
        self.pr_zones = [
            PRZoneConfig(
                zone_id=0,
                name='PR_0',
                gpio_pin='axi_gpio_0',
                address_ranges=[(0xA0000000, 0x10000), (0xA0010000, 0x10000)]
            ),
            PRZoneConfig(
                zone_id=1,
                name='PR_1', 
                gpio_pin='axi_gpio_1',
                address_ranges=[(0xA0100000, 0x10000), (0xA0110000, 0x10000)]
            )
        ]
        
        # Tenant di default
        self.tenants = {}
        tenant = TenantConfig(
            tenant_id='tenant1',
            uid=1000,
            gid=1000,
            api_key='test_key_1',
            max_overlays=2,
            max_buffers=10,
            max_memory_mb=256,
            allowed_bitstreams={
                'PR_0_sum.bit', 'PR_1_sum.bit', 
                'PR_0_mult.bit', 'PR_1_mult.bit',
                'PR_0_conv2d.bit', 'PR_1_conv2d.bit'
            },
            allowed_pr_zones={0, 1}
        )
        self.tenants['tenant1'] = tenant
        logger.info(f"Created default tenant: {tenant.tenant_id}")
    
    def add_tenant(self, tenant_config: TenantConfig) -> bool:
        """Aggiunge un nuovo tenant a runtime"""
        with self._lock:
            if tenant_config.tenant_id in self.tenants:
                logger.warning(f"Tenant {tenant_config.tenant_id} already exists")
                return False
            
            # Se allowed_pr_zones non è specificato, permetti tutte
            if not tenant_config.allowed_pr_zones:
                tenant_config.allowed_pr_zones = set(range(self.num_pr_zones))
            
            self.tenants[tenant_config.tenant_id] = tenant_config
            self._notify_watchers('tenant_added', tenant_config.tenant_id)
            self._save_to_file()
            
            logger.info(f"Added tenant {tenant_config.tenant_id}")
            return True
    
    def update_tenant(self, tenant_id: str, updates: dict) -> bool:
        """Aggiorna configurazione tenant"""
        with self._lock:
            if tenant_id not in self.tenants:
                logger.warning(f"Tenant {tenant_id} not found")
                return False
            
            tenant = self.tenants[tenant_id]
            
            # Aggiorna campi base
            if 'api_key' in updates:
                tenant.api_key = updates['api_key']
            
            # Aggiorna limiti
            if 'limits' in updates:
                limits = updates['limits']
                if 'max_overlays' in limits:
                    tenant.max_overlays = limits['max_overlays']
                if 'max_buffers' in limits:
                    tenant.max_buffers = limits['max_buffers']
                if 'max_memory_mb' in limits:
                    tenant.max_memory_mb = limits['max_memory_mb']
            
            # Gestione bitstreams
            if 'add_bitstreams' in updates:
                if tenant.allowed_bitstreams is None:
                    tenant.allowed_bitstreams = set()
                tenant.allowed_bitstreams.update(updates['add_bitstreams'])
            
            if 'remove_bitstreams' in updates:
                if tenant.allowed_bitstreams:
                    tenant.allowed_bitstreams -= set(updates['remove_bitstreams'])
            
            # Gestione PR zones
            if 'allowed_pr_zones' in updates:
                tenant.allowed_pr_zones = set(updates['allowed_pr_zones'])
            
            if 'add_pr_zones' in updates:
                if tenant.allowed_pr_zones is None:
                    tenant.allowed_pr_zones = set()
                tenant.allowed_pr_zones.update(updates['add_pr_zones'])
            
            if 'remove_pr_zones' in updates:
                if tenant.allowed_pr_zones:
                    tenant.allowed_pr_zones -= set(updates['remove_pr_zones'])
            
            self._notify_watchers('tenant_updated', tenant_id)
            self._save_to_file()
            
            logger.info(f"Updated tenant {tenant_id}")
            return True
    
    def remove_tenant(self, tenant_id: str) -> bool:
        """Rimuove tenant"""
        with self._lock:
            if tenant_id not in self.tenants:
                logger.warning(f"Tenant {tenant_id} not found")
                return False
            
            del self.tenants[tenant_id]
            self._notify_watchers('tenant_removed', tenant_id)
            self._save_to_file()
            
            logger.info(f"Removed tenant {tenant_id}")
            return True
    
    def add_allowed_bitstream(self, tenant_id: str, bitstream: str) -> bool:
        """Aggiunge bitstream permesso per tenant"""
        with self._lock:
            if tenant_id not in self.tenants:
                return False
            
            tenant = self.tenants[tenant_id]
            if tenant.allowed_bitstreams is None:
                tenant.allowed_bitstreams = set()
            
            tenant.allowed_bitstreams.add(bitstream)
            self._notify_watchers('bitstream_added', (tenant_id, bitstream))
            self._save_to_file()
            
            logger.info(f"Added bitstream {bitstream} for tenant {tenant_id}")
            return True
    
    def get_pr_zone_config(self, zone_id: int) -> Optional[PRZoneConfig]:
        """Ottieni configurazione di una PR zone"""
        for zone in self.pr_zones:
            if zone.zone_id == zone_id:
                return zone
        return None
    
    def get_pr_zone_addresses(self, zone_id: int) -> List[tuple]:
        """Ottieni gli indirizzi permessi per una PR zone"""
        zone = self.get_pr_zone_config(zone_id)
        return zone.address_ranges if zone else []
    
    def is_tenant_allowed_zone(self, tenant_id: str, zone_id: int) -> bool:
        """Verifica se un tenant può usare una specifica PR zone"""
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return False
        
        # Se non ha restrizioni, può usare tutte le zone
        if not tenant.allowed_pr_zones:
            return True
        
        return zone_id in tenant.allowed_pr_zones
    
    def get_tenant_allowed_zones(self, tenant_id: str) -> Set[int]:
        """Ottieni le PR zones permesse per un tenant"""
        tenant = self.tenants.get(tenant_id)
        if not tenant:
            return set()
        
        # Se non specificato, ritorna tutte le zone
        if not tenant.allowed_pr_zones:
            return set(range(self.num_pr_zones))
        
        return tenant.allowed_pr_zones.copy()
    
    def register_watcher(self, callback):
        """Registra callback per modifiche config"""
        self._config_watchers.append(callback)
    
    def _notify_watchers(self, event_type: str, data):
        """Notifica watchers di modifiche"""
        for watcher in self._config_watchers:
            try:
                watcher(event_type, data)
            except Exception as e:
                logger.error(f"Error notifying watcher: {e}")
    
    def _save_to_file(self):
        """Salva configurazione su file con nuovo formato"""
        if not self.config_file:
            logger.warning("No config file specified, not saving")
            return
        
        try:
            # Costruisci struttura globale
            global_config = {
                'num_pr_zones': self.num_pr_zones,
                'bitstream_dir': self.bitstream_dir,
                'socket_dir': self.socket_dir,
                'static_bitstream': self.static_bitstream,
                'pr_zones': []
            }
            
            # Aggiungi PR zones
            for zone in self.pr_zones:
                zone_dict = {
                    'zone_id': zone.zone_id,
                    'name': zone.name,
                    'gpio_pin': zone.dfx_decoupler,
                    'address_ranges': list(zone.address_ranges)
                }
                global_config['pr_zones'].append(zone_dict)
            
            # Costruisci dati completi
            data = {
                'global': global_config,
                'tenants': []
            }
            
            # Aggiungi tenants
            for tenant in self.tenants.values():
                tenant_dict = {
                    'id': tenant.tenant_id,
                    'uid': tenant.uid,
                    'gid': tenant.gid,
                    'api_key': tenant.api_key,
                    'max_overlays': tenant.max_overlays,
                    'max_buffers': tenant.max_buffers,
                    'max_memory_mb': tenant.max_memory_mb,
                    'allowed_bitstreams': list(tenant.allowed_bitstreams) if tenant.allowed_bitstreams else []
                }
                
                # Aggiungi allowed_pr_zones
                if tenant.allowed_pr_zones:
                    tenant_dict['allowed_pr_zones'] = list(tenant.allowed_pr_zones)
                
                data['tenants'].append(tenant_dict)
            
            # Atomic write
            temp_file = f"{self.config_file}.tmp"
            with open(temp_file, 'w') as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False)
            os.replace(temp_file, self.config_file)
            
            logger.info(f"Saved configuration to {self.config_file}")
            
        except Exception as e:
            logger.error(f"Error saving config: {e}")
    
    def reload_from_file(self):
        """Ricarica configurazione da file"""
        with self._lock:
            if self.config_file and os.path.exists(self.config_file):
                logger.info(f"Reloading configuration from {self.config_file}")
                old_tenants = set(self.tenants.keys())
                
                self._load_from_file()
                
                # Notifica cambiamenti
                new_tenants = set(self.tenants.keys())
                
                # Tenants aggiunti
                for tenant_id in new_tenants - old_tenants:
                    self._notify_watchers('tenant_added', tenant_id)
                
                # Tenants rimossi
                for tenant_id in old_tenants - new_tenants:
                    self._notify_watchers('tenant_removed', tenant_id)
                
                # Tenants potenzialmente modificati
                for tenant_id in old_tenants & new_tenants:
                    self._notify_watchers('tenant_updated', tenant_id)
    
    def get_config_summary(self) -> dict:
        """Ottieni riepilogo della configurazione"""
        return {
            'global': {
                'num_pr_zones': self.num_pr_zones,
                'bitstream_dir': self.bitstream_dir,
                'socket_dir': self.socket_dir,
                'static_bitstream': self.static_bitstream,
                'pr_zones_count': len(self.pr_zones)
            },
            'tenants_count': len(self.tenants),
            'tenants': {
                tenant_id: {
                    'uid': config.uid,
                    'gid': config.gid,
                    'max_overlays': config.max_overlays,
                    'max_buffers': config.max_buffers,
                    'max_memory_mb': config.max_memory_mb,
                    'allowed_bitstreams_count': len(config.allowed_bitstreams) if config.allowed_bitstreams else 0,
                    'allowed_pr_zones': list(config.allowed_pr_zones) if config.allowed_pr_zones else 'all'
                }
                for tenant_id, config in self.tenants.items()
            }
        }
    
    def validate_config(self) -> List[str]:
        """Valida la configurazione e ritorna lista di warning"""
        warnings = []
        
        # Verifica che ci siano PR zones definite
        if not self.pr_zones:
            warnings.append("No PR zones defined")
        
        # Verifica che il numero di PR zones corrisponda
        if len(self.pr_zones) != self.num_pr_zones:
            warnings.append(f"Number of PR zones ({len(self.pr_zones)}) doesn't match num_pr_zones ({self.num_pr_zones})")
        
        # Verifica che ogni tenant abbia almeno un bitstream permesso
        for tenant_id, tenant in self.tenants.items():
            if not tenant.allowed_bitstreams:
                warnings.append(f"Tenant {tenant_id} has no allowed bitstreams")
            
            # Verifica che le PR zones permesse siano valide
            if tenant.allowed_pr_zones:
                invalid_zones = tenant.allowed_pr_zones - set(range(self.num_pr_zones))
                if invalid_zones:
                    warnings.append(f"Tenant {tenant_id} has invalid PR zones: {invalid_zones}")
        
        # Verifica che i file esistano
        if not os.path.exists(self.static_bitstream):
            warnings.append(f"Static bitstream not found: {self.static_bitstream}")
        
        if not os.path.exists(self.bitstream_dir):
            warnings.append(f"Bitstream directory not found: {self.bitstream_dir}")
        
        return warnings