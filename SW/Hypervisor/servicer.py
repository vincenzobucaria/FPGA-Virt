# hypervisor/servicer.py
import grpc
import time
import logging
from typing import Dict
from tenant_manager import TenantManager, TenantResources
# Import generated proto
import sys
sys.path.append('../Proto/generated')
import pynq_service_pb2 as pb2
import pynq_service_pb2_grpc as pb2_grpc

from tenant_manager import TenantManager


logger = logging.getLogger(__name__)

class PYNQServicer(pb2_grpc.PYNQServiceServicer):
    def __init__(self, tenant_manager: TenantManager, resource_manager):
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
# Sostituisci il metodo LoadOverlay nel servicer con questo:

    def LoadOverlay(self, request, context):
        """Carica overlay e ritorna info incluso il device UIO"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"LoadOverlay request from {tenant_id}: {request.bitfile_path}")
        
        try:
            overlay_id, ip_cores = self.resource_manager.load_overlay(
                tenant_id, 
                request.bitfile_path
            )
            logger.info(f"Overlay loaded successfully: {overlay_id}")
            
            # Estrai metadata (zone_id e uio_device) dagli ip_cores
            zone_id = ip_cores.pop('_zone_id', None)
            uio_device = ip_cores.pop('_uio_device', None)
            
            # Converti IP cores in formato proto
            proto_ip_cores = {}
            for name, ip_info in ip_cores.items():
                proto_ip_core = pb2.IPCore(
                    name=ip_info['name'],
                    type=ip_info['type'],
                    base_address=ip_info['base_address'],
                    address_range=ip_info['address_range'],
                    parameters=ip_info['parameters']
                )
                
                # Aggiungi registri se presenti
                if 'registers' in ip_info and ip_info['registers']:
                    for reg_name, reg_info in ip_info['registers'].items():
                        proto_ip_core.registers[reg_name].offset = reg_info['offset']
                        proto_ip_core.registers[reg_name].description = reg_info.get('description', '')
                    
                    logger.debug(f"Added {len(ip_info['registers'])} registers to {name}")
                
                proto_ip_cores[name] = proto_ip_core
            
            # Costruisci risposta con info UIO device
            response = pb2.LoadOverlayResponse(
                overlay_id=overlay_id,
                ip_cores=proto_ip_cores
            )
            
            # NUOVO: Aggiungi info UIO device se disponibile
            if uio_device:
                response.uio_device = uio_device
                logger.info(f"UIO device {uio_device} assigned to tenant {tenant_id} for overlay {overlay_id}")
            
            if zone_id is not None:
                response.pr_zone_id = zone_id
                logger.info(f"PR zone {zone_id} allocated for overlay {overlay_id}")
            
            return response
            
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
    
    # MMIO operations - SEMPLIFICATO!
    def CreateMMIO(self, request, context):
        """Crea handle MMIO - ora molto più semplice!"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"CreateMMIO request from {tenant_id} for address 0x{request.base_address:08x}")
        
        try:
            # Chiama direttamente create_mmio senza overlay_id
            handle = self.resource_manager.create_mmio(
                tenant_id,
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
        t0 = time.perf_counter()
        tenant_id = self._get_tenant_id(context)
        t1 = time.perf_counter()
        try:
            self.resource_manager.mmio_write(
                tenant_id,
                request.handle,
                request.offset,
                request.value
            )
            t2 = time.perf_counter()
            logger.info(f"MMIO Write timing: auth={(t1-t0)*1000:.2f}ms, write={(t2-t1)*1000:.2f}ms")
            return pb2.Empty()
            
        except Exception as e:
            logger.error(f"MMIOWrite error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    # Buffer operations
    def AllocateBuffer(self, request, context):
        """Alloca buffer e ritorna info per il client"""
        tenant_id = self._get_tenant_id(context)
        
        # Parsing parametri
        shape = list(request.shape) if request.shape else [1024]  # default 1D
        dtype = request.dtype if request.dtype else 'uint8'
        
        logger.info(f"AllocateBuffer request from {tenant_id}: shape={shape}, dtype={dtype}")
        
        try:
            buffer_info = self.resource_manager.allocate_buffer(
                tenant_id,
                shape,
                dtype
            )
            
            # Costruisci risposta con info char device
            response = pb2.AllocateBufferResponse(
                handle=buffer_info['handle'],
                physical_address=buffer_info['physical_address'],
                size=buffer_info['total_size'],
                shape=buffer_info['shape'],
                dtype=buffer_info['dtype']
            )
            
            # Aggiungi info char device se disponibile
            if buffer_info.get('vm_offset') is not None:
                response.vm_offset = buffer_info['vm_offset']
                response.char_device_path = buffer_info.get('char_device', '')
            
            return response
            
        except Exception as e:
            logger.error(f"AllocateBuffer error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def ReadBuffer(self, request, context):
        """Leggi dati da buffer"""
        tenant_id = self._get_tenant_id(context)
        
        try:
            data = self.resource_manager.read_buffer(
                tenant_id,
                request.handle,
                request.offset,
                request.length
            )
            
            return pb2.ReadBufferResponse(data=data)
            
        except Exception as e:
            logger.error(f"ReadBuffer error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def WriteBuffer(self, request, context):
        """Scrivi dati in buffer"""
        tenant_id = self._get_tenant_id(context)
        
        try:
            self.resource_manager.write_buffer(
                tenant_id,
                request.handle,
                request.data,
                request.offset
            )
            
            return pb2.Empty()
            
        except Exception as e:
            logger.error(f"WriteBuffer error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))
    
    def FreeBuffer(self, request, context):
        """Libera buffer - TODO"""
        tenant_id = self._get_tenant_id(context)
        # TODO: Implementare deallocazione buffer
        return pb2.Empty()
    
    def CreateDMA(self, request, context):
        """Crea DMA handle - SEMPLIFICATO!"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"CreateDMA request from {tenant_id}: {request.dma_name}")
        
        try:
            # Chiama direttamente create_dma senza overlay_id
            handle, info = self.resource_manager.create_dma(
                tenant_id,
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
    
    def CleanupResources(self, request, context):
        """Pulisci tutte le risorse del tenant corrente"""
        tenant_id = self._get_tenant_id(context)
        logger.info(f"Cleanup resources request from tenant {tenant_id}")
        
        try:
            # Ottieni riepilogo prima del cleanup
            summary = self.resource_manager.get_tenant_resources_summary(tenant_id)
            
            # Esegui cleanup
            self.resource_manager.cleanup_tenant_resources(tenant_id)
            
            # Pulisci anche dal tenant manager
            if tenant_id in self.tenant_manager.resources:
                self.tenant_manager.resources[tenant_id] = TenantResources()
            
            message = (f"Cleaned up: {summary['overlays']} overlays, "
                    f"{summary['mmios']} MMIOs, {summary['buffers']} buffers, "
                    f"{summary['dmas']} DMAs, {summary['total_memory']} bytes")
            
            logger.info(f"Cleanup completed for {tenant_id}: {message}")
            
            # FIX: Costruisci correttamente resources_freed
            # Il proto si aspetta valori interi, non liste
            resources_freed = {
                'overlays': summary['overlays'],
                'mmios': summary['mmios'],
                'buffers': summary['buffers'],
                'dmas': summary['dmas'],
                'total_memory': summary['total_memory']
            }
            
            # Se il proto include pr_zones, aggiungi anche quello
            # ma verifica che sia il tipo giusto (potrebbe aspettarsi repeated int32)
            # Per ora lo omettiamo dal dict se causa problemi
            
            return pb2.CleanupResponse(
                success=True,
                message=message,
                resources_freed=resources_freed
            )
            
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
            import traceback
            logger.error(traceback.format_exc())
            
            return pb2.CleanupResponse(
                success=False,
                message=str(e),
                resources_freed={}  # Dict vuoto in caso di errore
            )  