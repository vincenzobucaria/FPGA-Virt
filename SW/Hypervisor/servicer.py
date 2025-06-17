# hypervisor/servicer.py
import grpc
import time
import logging
from typing import Dict

# Import generated proto
import sys
sys.path.append('../Proto/generated')
import pynq_service_pb2 as pb2
import pynq_service_pb2_grpc as pb2_grpc

from tenant_manager import TenantManager
from mock_resource_manager import MockResourceManager as ResourceManager

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
            logger.info(f"Overlay loaded successfully: {overlay_id}")
            
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
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def GetOverlayInfo(self, request, context):
        """Ottieni info overlay"""
        tenant_id = self._get_tenant_id(context)
        
        # TODO: Implementare recupero info overlay
        return pb2.OverlayInfoResponse(
            overlay_id=request.overlay_id,
            loaded_at=int(time.time())
        )
    
    # MMIO operations
    def CreateMMIO(self, request, context):
        """Crea handle MMIO"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"CreateMMIO request from {tenant_id}")
        
        overlay_id = request.overlay_id
        
        # Se l'overlay_id è "current", trova l'ultimo overlay caricato dal tenant
        if overlay_id == "current":
            # Trova l'ultimo overlay del tenant
            tenant_overlays = [
                (handle, res) for handle, res in self.resource_manager._resources.items()
                if res.tenant_id == tenant_id and res.resource_type == "overlay"
            ]
            
            if tenant_overlays:
                # Prendi il più recente basandosi su created_at
                overlay_id = max(tenant_overlays, key=lambda x: x[1].created_at)[0]
                logger.info(f"Found current overlay: {overlay_id}")
            else:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "No overlay loaded")
        
        try:
            # Ora overlay_id contiene l'handle corretto
            handle = self.resource_manager.create_mmio(
                tenant_id,
                overlay_id,  # Usa l'overlay_id risolto
                request.ip_name,
                request.base_address,
                request.length
            )
            
            logger.info(f"MMIO created successfully: {handle}")
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
        """Leggi da buffer - TODO"""
        tenant_id = self._get_tenant_id(context)
        # TODO: Implementare lettura buffer
        return pb2.ReadBufferResponse(data=b'')
    
    def WriteBuffer(self, request, context):
        """Scrivi su buffer - TODO"""
        tenant_id = self._get_tenant_id(context)
        # TODO: Implementare scrittura buffer
        return pb2.Empty()
    
    def FreeBuffer(self, request, context):
        """Libera buffer - TODO"""
        tenant_id = self._get_tenant_id(context)
        # TODO: Implementare deallocazione buffer
        return pb2.Empty()
    
    def CreateDMA(self, request, context):
        """Crea DMA handle"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"CreateDMA request from {tenant_id}")
        
        overlay_id = request.overlay_id
        
        # Gestisci "current" come in CreateMMIO
        if overlay_id == "current":
            tenant_overlays = [
                (handle, res) for handle, res in self.resource_manager._resources.items()
                if res.tenant_id == tenant_id and res.resource_type == "overlay"
            ]
            
            if tenant_overlays:
                overlay_id = max(tenant_overlays, key=lambda x: x[1].created_at)[0]
                logger.info(f"Found current overlay for DMA: {overlay_id}")
            else:
                context.abort(grpc.StatusCode.FAILED_PRECONDITION, "No overlay loaded")
        
        try:
            handle, info = self.resource_manager.create_dma(
                tenant_id,
                overlay_id,
                request.dma_name
            )
            
            return pb2.CreateDMAResponse(
                handle=handle,
                has_send_channel=info['has_send_channel'],
                has_recv_channel=info['has_recv_channel'],
                max_transfer_size=info['max_transfer_size']
            )
            
        except Exception as e:
            logger.error(f"CreateDMA error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def DMATransfer(self, request, context):
        """Esegui trasferimento DMA - TODO"""
        tenant_id = self._get_tenant_id(context)
        # TODO: Implementare trasferimento DMA
        return pb2.DMATransferResponse(transfer_id="transfer_todo")
    
    def GetDMAStatus(self, request, context):
        """Ottieni stato DMA - TODO"""
        tenant_id = self._get_tenant_id(context)
        # TODO: Implementare stato DMA
        return pb2.GetDMAStatusResponse(status=0)