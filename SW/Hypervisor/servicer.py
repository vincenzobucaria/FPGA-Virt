# hypervisor/servicer.py
import grpc
import time
import logging
from typing import Dict

# Import generated proto
import sys
sys.path.append('./generated')
import pynq_service_pb2 as pb2
import pynq_service_pb2_grpc as pb2_grpc

from .tenant_manager import TenantManager
from .resource_manager import ResourceManager

logger = logging.getLogger(__name__)

class PYNQServicer(pb2_grpc.PYNQServiceServicer):
    def __init__(self, tenant_manager: TenantManager, resource_manager: ResourceManager):
        self.tenant_manager = tenant_manager
        self.resource_manager = resource_manager
        logger.info("PYNQServicer initialized")
    
    def _get_tenant_id(self, context) -> str:
        """Estrai tenant_id dal token nei metadata"""
        metadata = dict(context.invocation_metadata())
        token = metadata.get('auth-token')
        
        if not token:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, 'Missing auth token')
            
        tenant_id = self.tenant_manager.validate_token(token)
        if not tenant_id:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, 'Invalid or expired token')
            
        return tenant_id
    
    # Session management
    def Authenticate(self, request, context):
        """Autentica tenant"""
        logger.info(f"Authentication request from tenant: {request.tenant_id}")
        
        token = self.tenant_manager.authenticate(request.tenant_id, request.api_key)
        
        if not token:
            return pb2.AuthResponse(
                success=False,
                message="Authentication failed"
            )
        
        return pb2.AuthResponse(
            success=True,
            session_token=token,
            message="Authentication successful",
            expires_at=int(time.time() + 3600)
        )
    
    # Overlay operations
    def LoadOverlay(self, request, context):
        """Carica overlay"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"LoadOverlay request from {tenant_id}: {request.bitfile_path}")
        
        try:
            overlay_id, ip_cores = self.resource_manager.load_overlay(
                tenant_id, 
                request.bitfile_path
            )
            
            # Converti IP cores in formato proto
            proto_ip_cores = {}
            for name, ip_info in ip_cores.items():
                proto_ip_cores[name] = pb2.IPCore(
                    name=ip_info['name'],
                    type=ip_info['type'],
                    base_address=ip_info['base_address'],
                    address_range=ip_info['address_range'],
                    parameters=ip_info['parameters']
                )
            
            return pb2.LoadOverlayResponse(
                overlay_id=overlay_id,
                ip_cores=proto_ip_cores
            )
            
        except Exception as e:
            logger.error(f"LoadOverlay error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def GetOverlayInfo(self, request, context):
        """Ottieni info overlay"""
        tenant_id = self._get_tenant_id(context)
        
        # TODO: Implementare
        return pb2.OverlayInfoResponse(
            overlay_id=request.overlay_id,
            loaded_at=int(time.time())
        )
    
    # MMIO operations
    def CreateMMIO(self, request, context):
        """Crea handle MMIO"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"CreateMMIO request from {tenant_id}")
        
        try:
            handle = self.resource_manager.create_mmio(
                tenant_id,
                request.overlay_id,
                request.ip_name,
                request.base_address,
                request.length
            )
            
            return pb2.CreateMMIOResponse(handle=handle)
            
        except Exception as e:
            logger.error(f"CreateMMIO error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def MMIORead(self, request, context):
        """Leggi da MMIO"""
        tenant_id = self._get_tenant_id(context)
        
        try:
            value = self.resource_manager.mmio_read(
                tenant_id,
                request.handle,
                request.offset,
                request.length
            )
            
            return pb2.MMIOReadResponse(value=value)
            
        except Exception as e:
            logger.error(f"MMIORead error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def MMIOWrite(self, request, context):
        """Scrivi su MMIO"""
        tenant_id = self._get_tenant_id(context)
        
        try:
            self.resource_manager.mmio_write(
                tenant_id,
                request.handle,
                request.offset,
                request.value
            )
            
            return pb2.Empty()
            
        except Exception as e:
            logger.error(f"MMIOWrite error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    # Buffer operations
    def AllocateBuffer(self, request, context):
        """Alloca buffer"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"AllocateBuffer request from {tenant_id}: {request.size} bytes")
        
        try:
            handle, phys_addr = self.resource_manager.allocate_buffer(
                tenant_id,
                request.size,
                request.buffer_type
            )
            
            return pb2.AllocateBufferResponse(
                handle=handle,
                physical_address=phys_addr,
                size=request.size
            )
            
        except Exception as e:
            logger.error(f"AllocateBuffer error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    # Altri metodi da implementare...
    def ReadBuffer(self, request, context):
        # TODO
        return pb2.ReadBufferResponse(data=b'')
    
    def WriteBuffer(self, request, context):
        # TODO
        return pb2.Empty()
    
    def FreeBuffer(self, request, context):
        # TODO
        return pb2.Empty()
    
    def CreateDMA(self, request, context):
        # TODO
        return pb2.CreateDMAResponse(handle="dma_todo")
    
    def DMATransfer(self, request, context):
        # TODO
        return pb2.DMATransferResponse(transfer_id="transfer_todo")
    
    def GetDMAStatus(self, request, context):
        # TODO
        return pb2.GetDMAStatusResponse(status=0)