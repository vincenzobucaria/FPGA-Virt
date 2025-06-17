# client/pynq_proxy/overlay.py
from typing import Dict, Any
import logging
import sys
from client.pynq_proxy.mmio import MMIO 
from client.connection import Connection

import pynq_service_pb2 as pb2

logger = logging.getLogger(__name__)

class Overlay:
    """PYNQ Overlay proxy implementation"""
    
    def __init__(self, bitfile_name: str, download: bool = True, ignore_version: bool = False):
        self._connection = Connection()
        self._bitfile_name = bitfile_name
        
        # Carica overlay sul server
        request = pb2.LoadOverlayRequest(
            bitfile_path=bitfile_name,
            download=download,
            partial_reconfiguration=False
        )
        
        response = self._connection.call_with_auth('LoadOverlay', request)
        
        if response.error:
            raise Exception(f"Failed to load overlay: {response.error}")
            
        self._overlay_id = response.overlay_id
        self._ip_dict = self._parse_ip_dict(response.ip_cores)
        
        # Crea attributi per ogni IP
        self._create_ip_attributes()
        
        logger.info(f"Overlay {bitfile_name} loaded with ID: {self._overlay_id}")
        
    def _parse_ip_dict(self, ip_cores_proto) -> Dict[str, Dict[str, Any]]:
        """Converte proto IP dict in Python dict"""
        ip_dict = {}
        
        for name, ip_core in ip_cores_proto.items():
            ip_dict[name] = {
                'name': ip_core.name,
                'type': ip_core.type,
                'phys_addr': ip_core.base_address,
                'addr_range': ip_core.address_range,
                'parameters': dict(ip_core.parameters)
            }
            
        return ip_dict
        
    def _create_ip_attributes(self):
        """Crea attributi per accesso diretto agli IP"""
        for name, ip_info in self._ip_dict.items():
            
            """ 
            
            if 'gpio' in ip_info['type'].lower():
                # Crea GPIO proxy
                from .gpio import GPIO
                setattr(self, name, GPIO(
                    self._overlay_id,
                    name,
                    ip_info['phys_addr'],
                    self._connection
                ))
            elif 'dma' in ip_info['type'].lower():
                # Crea DMA proxy
                from .dma import DMA
                setattr(self, name, DMA(
                    self._overlay_id,
                    name,
                    ip_info['phys_addr'],
                    self._connection
                ))
            
            else:
            """
            # Default: MMIO
            
            mmio_obj = MMIO(
            base_addr=ip_info['phys_addr'],  # Solo base_addr
            length=ip_info['addr_range']      # e length!
            )
            mmio_obj._overlay_id = self._overlay_id
            mmio_obj._ip_name = name
            setattr(self, name, mmio_obj)
    
    @property
    def ip_dict(self):
        """Ritorna dizionario degli IP cores"""
        return self._ip_dict
        
    @property
    def bitfile_name(self):
        """Ritorna nome del bitfile"""
        return self._bitfile_name