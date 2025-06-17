# hypervisor/management_service.py
import grpc
import logging
from typing import Dict
import sys

sys.path.insert(0, '../Proto/generated')

import pynq_service_pb2 as pb2
import pynq_service_pb2_grpc as pb2_grpc
logger = logging.getLogger(__name__)

class ManagementServicer(pb2_grpc.PYNQManagementServiceServicer):
    def __init__(self, server_instance):
        self.server = server_instance
        self.config_manager = server_instance.config_manager
        self.tenant_manager = server_instance.tenant_manager
        
    def CreateTenant(self, request, context):
        """Crea nuovo tenant a runtime"""
        # Verifica che non esista già
        if request.tenant_id in self.config_manager.tenants:
            return pb2.CreateTenantResponse(
                success=False,
                message=f"Tenant {request.tenant_id} already exists"
            )
        
        # Crea configurazione tenant
        from .config import TenantConfig
        
        tenant_config = TenantConfig(
            tenant_id=request.tenant_id,
            uid=request.uid,
            gid=request.gid,
            api_key=request.api_key,
            max_overlays=request.limits.max_overlays if request.limits else 2,
            max_buffers=request.limits.max_buffers if request.limits else 10,
            max_memory_mb=request.limits.max_memory_mb if request.limits else 256,
            allowed_bitstreams=set(request.allowed_bitstreams),
            allowed_address_ranges=[
                (r.start, r.end) for r in request.allowed_address_ranges
            ]
        )
        
        # Aggiungi a config manager
        if not self.config_manager.add_tenant(tenant_config):
            return pb2.CreateTenantResponse(
                success=False,
                message="Failed to add tenant"
            )
        
        # Crea server per il nuovo tenant
        try:
            self.server.create_and_start_tenant_server(request.tenant_id)
            
            socket_path = f"{self.config_manager.socket_dir}/{request.tenant_id}.sock"
            
            return pb2.CreateTenantResponse(
                success=True,
                message=f"Tenant {request.tenant_id} created successfully",
                socket_path=socket_path
            )
            
        except Exception as e:
            # Rollback
            self.config_manager.remove_tenant(request.tenant_id)
            
            return pb2.CreateTenantResponse(
                success=False,
                message=f"Failed to create server: {str(e)}"
            )
    
    def UpdateTenant(self, request, context):
        """Aggiorna configurazione tenant"""
        updates = {}
        
        if request.updates.api_key:
            updates['api_key'] = request.updates.api_key
            
        if request.updates.limits:
            updates['limits'] = {
                'max_overlays': request.updates.limits.max_overlays,
                'max_buffers': request.updates.limits.max_buffers,
                'max_memory_mb': request.updates.limits.max_memory_mb
            }
            
        if request.updates.add_bitstreams:
            updates['add_bitstreams'] = list(request.updates.add_bitstreams)
            
        if request.updates.remove_bitstreams:
            updates['remove_bitstreams'] = list(request.updates.remove_bitstreams)
        
        success = self.config_manager.update_tenant(request.tenant_id, updates)
        
        return pb2.UpdateTenantResponse(
            success=success,
            message="Tenant updated" if success else "Tenant not found"
        )
    
    def DeleteTenant(self, request, context):
        """Elimina tenant"""
        # Controlla risorse attive
        if not request.force:
            resources = self.tenant_manager.resources.get(request.tenant_id)
            if resources and (resources.overlays or resources.buffers):
                return pb2.DeleteTenantResponse(
                    success=False,
                    message="Tenant has active resources. Use force=true to delete anyway"
                )
        
        # Stop server del tenant
        self.server.stop_tenant_server(request.tenant_id)
        
        # Rimuovi da config
        success = self.config_manager.remove_tenant(request.tenant_id)
        
        # Cleanup risorse
        if success:
            self.server.resource_manager.cleanup_tenant_resources(request.tenant_id)
        
        return pb2.DeleteTenantResponse(
            success=success,
            message="Tenant deleted" if success else "Tenant not found"
        )
    
    def AddAllowedBitstream(self, request, context):
        """Aggiunge bitstream permesso"""
        success = self.config_manager.add_allowed_bitstream(
            request.tenant_id,
            request.bitstream
        )
        
        if not success:
            context.abort(grpc.StatusCode.NOT_FOUND, "Tenant not found")
            
        return pb2.Empty()
    
    def GetTenantStatus(self, request, context):
        """Ottieni status tenant"""
        if request.tenant_id not in self.config_manager.tenants:
            context.abort(grpc.StatusCode.NOT_FOUND, "Tenant not found")
        
        # Raccogli info
        resources = self.tenant_manager.resources.get(request.tenant_id)
        config = self.config_manager.tenants[request.tenant_id]
        
        status = pb2.TenantStatus(
            online=request.tenant_id in self.server.servers,
            active_overlays=len(resources.overlays) if resources else 0,
            active_buffers=len(resources.buffer_handles) if resources else 0,
            memory_used_bytes=resources.total_memory_bytes if resources else 0
        )
        
        info = pb2.TenantInfo(
            tenant_id=request.tenant_id,
            uid=config.uid,
            gid=config.gid,
            limits=pb2.CreateTenantRequest.Limits(
                max_overlays=config.max_overlays,
                max_buffers=config.max_buffers,
                max_memory_mb=config.max_memory_mb
            ),
            allowed_bitstreams=list(config.allowed_bitstreams) if config.allowed_bitstreams else [],
            status=status
        )
        
        return pb2.GetTenantStatusResponse(info=info)