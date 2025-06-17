# hypervisor/config_manager.py
import os
import yaml
import threading
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, asdict
import logging
from config import TenantConfig
logger = logging.getLogger(__name__)

class DynamicConfigManager:
    def __init__(self, config_file: str = None):
        self.config_file = config_file
        self._lock = threading.RLock()
        self._config_watchers = []
        
        # Carica config iniziale
        if config_file and os.path.exists(config_file):
            self._load_from_file()
        else:
            self.tenants = {}
            
        # Settings globali
        self.socket_dir = os.environ.get('PYNQ_SOCKET_DIR', '/var/run/pynq')
        self.bitstream_dir = os.environ.get('PYNQ_BITSTREAM_DIR', '/opt/bitstreams')
        
    def add_tenant(self, tenant_config: TenantConfig) -> bool:
        """Aggiunge un nuovo tenant a runtime"""
        with self._lock:
            if tenant_config.tenant_id in self.tenants:
                return False
                
            self.tenants[tenant_config.tenant_id] = tenant_config
            self._notify_watchers('tenant_added', tenant_config.tenant_id)
            self._save_to_file()
            
            logger.info(f"Added tenant {tenant_config.tenant_id}")
            return True
    
    def update_tenant(self, tenant_id: str, updates: dict) -> bool:
        """Aggiorna configurazione tenant"""
        with self._lock:
            if tenant_id not in self.tenants:
                return False
                
            tenant = self.tenants[tenant_id]
            
            # Aggiorna campi
            if 'api_key' in updates:
                tenant.api_key = updates['api_key']
                
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
            
            self._notify_watchers('tenant_updated', tenant_id)
            self._save_to_file()
            
            logger.info(f"Updated tenant {tenant_id}")
            return True
    
    def remove_tenant(self, tenant_id: str) -> bool:
        """Rimuove tenant"""
        with self._lock:
            if tenant_id not in self.tenants:
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
        """Salva configurazione su file"""
        if not self.config_file:
            return
            
        try:
            data = {
                'tenants': []
            }
            
            for tenant in self.tenants.values():
                tenant_dict = {
                    'id': tenant.tenant_id,
                    'uid': tenant.uid,
                    'gid': tenant.gid,
                    'api_key': tenant.api_key,
                    'max_overlays': tenant.max_overlays,
                    'max_buffers': tenant.max_buffers,
                    'max_memory_mb': tenant.max_memory_mb,
                    'allowed_bitstreams': list(tenant.allowed_bitstreams) if tenant.allowed_bitstreams else [],
                    'allowed_address_ranges': list(tenant.allowed_address_ranges) if tenant.allowed_address_ranges else []
                }
                data['tenants'].append(tenant_dict)
            
            # Atomic write
            temp_file = f"{self.config_file}.tmp"
            with open(temp_file, 'w') as f:
                yaml.dump(data, f, default_flow_style=False)
            os.replace(temp_file, self.config_file)
            
            logger.info(f"Saved configuration to {self.config_file}")
            
        except Exception as e:
            logger.error(f"Error saving config: {e}")