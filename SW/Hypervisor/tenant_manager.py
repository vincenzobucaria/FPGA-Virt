# hypervisor/tenant_manager.py
import threading
import time
from typing import Dict, Optional, Set
from dataclasses import dataclass, field
from config import TenantConfig

@dataclass
class TenantSession:
    tenant_id: str
    token: str
    created_at: float
    expires_at: float
    
@dataclass
class TenantResources:
    overlays: Set[str] = field(default_factory=set)
    mmio_handles: Set[str] = field(default_factory=set)
    buffer_handles: Set[str] = field(default_factory=set)
    dma_handles: Set[str] = field(default_factory=set)
    total_memory_bytes: int = 0

class TenantManager:
    def __init__(self, config: Dict[str, TenantConfig]):
        self.config = config
        self.sessions: Dict[str, TenantSession] = {}
        self.resources: Dict[str, TenantResources] = {}
        self._lock = threading.RLock()
        
        # Inizializza risorse per ogni tenant
        for tenant_id in config:
            self.resources[tenant_id] = TenantResources()
    
    def authenticate(self, tenant_id: str, api_key: str) -> Optional[str]:
        """Autentica tenant e ritorna token"""
        with self._lock:
            tenant_config = self.config.get(tenant_id)
            if not tenant_config:
                return None
                
            if tenant_config.api_key and tenant_config.api_key != api_key:
                return None
            
            # Genera token
            import uuid
            token = f"{tenant_id}_{uuid.uuid4().hex}"
            
            # Crea sessione
            session = TenantSession(
                tenant_id=tenant_id,
                token=token,
                created_at=time.time(),
                expires_at=time.time() + 3600  # 1 ora
            )
            
            self.sessions[token] = session
            return token
    
    def validate_token(self, token: str) -> Optional[str]:
        """Valida token e ritorna tenant_id"""
        with self._lock:
            session = self.sessions.get(token)
            if not session:
                return None
                
            if time.time() > session.expires_at:
                del self.sessions[token]
                return None
                
            return session.tenant_id
    
    def can_allocate_overlay(self, tenant_id: str) -> bool:
        """Controlla se il tenant può allocare un altro overlay"""
        with self._lock:
            config = self.config[tenant_id]
            resources = self.resources[tenant_id]
            return len(resources.overlays) < config.max_overlays
    
    def can_allocate_buffer(self, tenant_id: str, size: int) -> bool:
        """Controlla se il tenant può allocare un buffer"""
        with self._lock:
            config = self.config[tenant_id]
            resources = self.resources[tenant_id]
            
            if len(resources.buffer_handles) >= config.max_buffers:
                return False
                
            max_bytes = config.max_memory_mb * 1024 * 1024
            if resources.total_memory_bytes + size > max_bytes:
                return False
                
            return True
    
    def is_bitstream_allowed(self, tenant_id: str, bitstream: str) -> bool:
        """Controlla se il tenant può usare questo bitstream"""
        config = self.config[tenant_id]
        if not config.allowed_bitstreams:
            return True  # Nessuna restrizione
        return bitstream in config.allowed_bitstreams
    
    def is_address_allowed(self, tenant_id: str, address: int, size: int) -> bool:
        """Controlla se il tenant può accedere a questo range di indirizzi"""
        config = self.config[tenant_id]
        if not config.allowed_address_ranges:
            return True  # Nessuna restrizione
            
        for start, end in config.allowed_address_ranges:
            if start <= address and (address + size) <= end:
                return True
        return False