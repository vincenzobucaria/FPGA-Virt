# hypervisor/config.py
import os
import yaml
from dataclasses import dataclass
from typing import Dict, List, Set

@dataclass
class TenantConfig:
    tenant_id: str
    uid: int
    gid: int
    api_key: str
    max_overlays: int = 2
    max_buffers: int = 10
    max_memory_mb: int = 256
    allowed_bitstreams: Set[str] = None
    allowed_address_ranges: List[tuple] = None

class Config:
    def __init__(self, config_file: str = None):
        self.socket_dir = os.environ.get('PYNQ_SOCKET_DIR', '/var/run/pynq')
        self.bitstream_dir = os.environ.get('PYNQ_BITSTREAM_DIR', '/opt/bitstreams')
        self.tenants: Dict[str, TenantConfig] = {}
        
        if config_file and os.path.exists(config_file):
            self._load_config(config_file)
        else:
            self._load_default_config()
    
    def _load_config(self, config_file: str):
        with open(config_file, 'r') as f:
            data = yaml.safe_load(f)
            
        for tenant_data in data.get('tenants', []):
            tenant = TenantConfig(
                tenant_id=tenant_data['id'],
                uid=tenant_data['uid'],
                gid=tenant_data['gid'],
                api_key=tenant_data.get('api_key', ''),
                max_overlays=tenant_data.get('max_overlays', 2),
                max_buffers=tenant_data.get('max_buffers', 10),
                max_memory_mb=tenant_data.get('max_memory_mb', 256),
                allowed_bitstreams=set(tenant_data.get('allowed_bitstreams', [])),
                allowed_address_ranges=[
                    tuple(r) for r in tenant_data.get('allowed_address_ranges', [])
                ]
            )
            self.tenants[tenant.tenant_id] = tenant
    
    def _load_default_config(self):
        # Configurazione di default per testing
        self.tenants['tenant1'] = TenantConfig(
            tenant_id='tenant1',
            uid=1001,
            gid=1001,
            api_key='test_key_1',
            allowed_bitstreams={'base.bit', 'conv2d.bit'},
            allowed_address_ranges=[(0xA0000000, 0xA0010000)]
        )