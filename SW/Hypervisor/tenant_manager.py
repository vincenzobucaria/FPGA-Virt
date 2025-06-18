# hypervisor/tenant_manager.py
import threading
import time
from typing import Dict, Optional, Set
from dataclasses import dataclass, field
import logging
from config import TenantConfig

logger = logging.getLogger(__name__)

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
            
        logger.info(f"TenantManager initialized with {len(config)} tenants")
    
    def authenticate(self, tenant_id: str, api_key: str) -> Optional[str]:
        """Autentica tenant e ritorna token"""
        with self._lock:
            tenant_config = self.config.get(tenant_id)
            if not tenant_config:
                logger.warning(f"Authentication failed: tenant {tenant_id} not found")
                return None
                
            if tenant_config.api_key and tenant_config.api_key != api_key:
                logger.warning(f"Authentication failed: invalid API key for tenant {tenant_id}")
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
            logger.info(f"Authentication successful for tenant {tenant_id}")
            return token
    
    def validate_token(self, token: str) -> Optional[str]:
        """Valida token e ritorna tenant_id"""
        with self._lock:
            session = self.sessions.get(token)
            if not session:
                logger.debug(f"Token validation failed: token not found")
                return None
                
            if time.time() > session.expires_at:
                del self.sessions[token]
                logger.debug(f"Token validation failed: token expired for tenant {session.tenant_id}")
                return None
            
            logger.debug(f"Token validated for tenant {session.tenant_id}")
            return session.tenant_id
    
    def can_allocate_overlay(self, tenant_id: str) -> bool:
        """Controlla se il tenant può allocare un altro overlay"""
        with self._lock:
            config = self.config[tenant_id]
            resources = self.resources[tenant_id]
            can_allocate = len(resources.overlays) < config.max_overlays
            
            logger.debug(f"Tenant {tenant_id} overlay allocation check: "
                        f"{len(resources.overlays)}/{config.max_overlays} used, "
                        f"can_allocate={can_allocate}")
            return can_allocate
    
    def can_allocate_buffer(self, tenant_id: str, size: int) -> bool:
        """Controlla se il tenant può allocare un buffer"""
        with self._lock:
            config = self.config[tenant_id]
            resources = self.resources[tenant_id]
            
            # Check buffer count
            if len(resources.buffer_handles) >= config.max_buffers:
                logger.warning(f"Tenant {tenant_id} reached buffer limit: "
                              f"{len(resources.buffer_handles)}/{config.max_buffers}")
                return False
            
            # Check memory limit
            max_bytes = config.max_memory_mb * 1024 * 1024
            if resources.total_memory_bytes + size > max_bytes:
                logger.warning(f"Tenant {tenant_id} would exceed memory limit: "
                              f"current={resources.total_memory_bytes/1024/1024:.1f}MB, "
                              f"requested={size/1024/1024:.1f}MB, "
                              f"max={config.max_memory_mb}MB")
                return False
            
            logger.debug(f"Tenant {tenant_id} can allocate buffer of {size} bytes")
            return True
    
    def is_bitstream_allowed(self, tenant_id: str, bitstream: str) -> bool:
        """Controlla se il tenant può usare questo bitstream"""
        config = self.config[tenant_id]
        if not config.allowed_bitstreams:
            logger.debug(f"Tenant {tenant_id} has no bitstream restrictions")
            return True  # Nessuna restrizione
        
        allowed = bitstream in config.allowed_bitstreams
        logger.debug(f"Tenant {tenant_id} bitstream '{bitstream}' allowed: {allowed}")
        return allowed
    
    def is_address_allowed(self, tenant_id: str, address: int, size: int) -> bool:
        """Controlla se il tenant può accedere a questo range di indirizzi"""
        config = self.config[tenant_id]
        if not config.allowed_address_ranges:
            logger.debug(f"Tenant {tenant_id} has no address restrictions")
            return True  # Nessuna restrizione
        
        logger.debug(f"Checking address 0x{address:08X} size 0x{size:X} for tenant {tenant_id}")    
        
        # FIX: interpreta correttamente come (base, size) non (start, end)
        for base, range_size in config.allowed_address_ranges:
            # Verifica se il range richiesto è dentro uno permesso
            if address >= base and (address + size) <= (base + range_size):
                logger.debug(f"Address 0x{address:08X} allowed - within range "
                           f"0x{base:08X}-0x{base + range_size:08X}")
                return True
        
        # Se arriviamo qui, l'indirizzo non è permesso
        logger.warning(f"Address check FAILED for tenant {tenant_id}:")
        logger.warning(f"  Requested: 0x{address:08X} - 0x{address + size:08X} (size 0x{size:X})")
        logger.warning(f"  Allowed ranges:")
        for base, range_size in config.allowed_address_ranges:
            logger.warning(f"    0x{base:08X} - 0x{base + range_size:08X}")
        
        return False
    
    def reset_tenant_resources(self, tenant_id: str):
        """Reset risorse tracked per un tenant dopo cleanup"""
        with self._lock:
            if tenant_id in self.resources:
                old_resources = self.resources[tenant_id]
                self.resources[tenant_id] = TenantResources()
                logger.info(f"Reset resource tracking for tenant {tenant_id}. "
                           f"Previous state: {len(old_resources.overlays)} overlays, "
                           f"{len(old_resources.buffer_handles)} buffers, "
                           f"{old_resources.total_memory_bytes/1024/1024:.1f}MB memory")