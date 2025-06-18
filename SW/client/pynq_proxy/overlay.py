# client/pynq_proxy/overlay.py
from typing import Dict, Any
import logging
import sys
from client.pynq_proxy.mmio import MMIO 
from client.connection import Connection

import pynq_service_pb2 as pb2

logger = logging.getLogger(__name__)

class RegisterMap:
    """Register map per accesso ai registri per nome - compatibile PYNQ"""
    
    def __init__(self, mmio, registers):
        self._mmio = mmio
        self._registers = registers
    
    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        elif name in self._registers:
            offset = self._registers[name]['offset']
            self._mmio.write(offset, value)
        else:
            raise AttributeError(f"No register named '{name}'")
    
    def __getattr__(self, name):
        if name in self._registers:
            offset = self._registers[name]['offset']
            return self._mmio.read(offset)
        raise AttributeError(f"No register named '{name}'")
    
    def __dir__(self):
        """Per autocompletamento in Jupyter"""
        return list(self._registers.keys())
    
    def __repr__(self):
        """Mostra lista registri quando si stampa l'oggetto - COME PYNQ!"""
        output = "RegisterMap {\n"
        
        # Ordina registri per offset
        sorted_regs = sorted(self._registers.items(), 
                           key=lambda x: x[1]['offset'])
        
        for reg_name, reg_info in sorted_regs:
            offset = reg_info['offset']
            desc = reg_info.get('description', '')
            try:
                value = getattr(self, reg_name)
                output += f"  {reg_name:<15} : 0x{offset:04X} -> 0x{value:08X}"
                if desc:
                    output += f" ({desc})"
                output += "\n"
            except:
                output += f"  {reg_name:<15} : 0x{offset:04X} -> <error>"
                if desc:
                    output += f" ({desc})"
                output += "\n"
        
        output += "}"
        return output
    
    def __str__(self):
        """Alias per __repr__"""
        return self.__repr__()


class IPCore(MMIO):
    """IP Core wrapper che estende MMIO con register_map - compatibile PYNQ"""
    
    def __init__(self, base_addr, length, ip_name=None, overlay_id=None, registers=None):
        super().__init__(base_addr, length)
        self._ip_name = ip_name
        self._overlay_id = overlay_id
        
        # Se ci sono registri, crea register_map
        if registers:
            self.register_map = RegisterMap(self, registers)
    
    def __repr__(self):
        """Rappresentazione dell'IP core"""
        output = f"{self._ip_name or 'IPCore'} @ 0x{self.base_addr:08X}\n"
        if hasattr(self, 'register_map'):
            output += "Registers:\n"
            # Mostra primi registri
            regs = sorted(self.register_map._registers.items(), 
                         key=lambda x: x[1]['offset'])[:5]
            for name, info in regs:
                output += f"  {name}: offset 0x{info['offset']:04X}\n"
            if len(self.register_map._registers) > 5:
                output += f"  ... and {len(self.register_map._registers) - 5} more\n"
        return output


class Overlay:
    """PYNQ Overlay proxy implementation con cleanup automatico"""
    
    def __init__(self, bitfile_name: str, download: bool = True, ignore_version: bool = False):
        self._connection = Connection()
        self._bitfile_name = bitfile_name
        self._closed = False
        
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
            ip_info = {
                'name': ip_core.name,
                'type': ip_core.type,
                'phys_addr': ip_core.base_address,
                'addr_range': ip_core.address_range,
                'parameters': dict(ip_core.parameters)
            }
            
            # Parsing dei registri se presenti
            if hasattr(ip_core, 'registers') and ip_core.registers:
                ip_info['registers'] = {}
                for reg_name, reg_info in ip_core.registers.items():
                    ip_info['registers'][reg_name] = {
                        'offset': reg_info.offset,
                        'description': reg_info.description if hasattr(reg_info, 'description') else ''
                    }
                logger.debug(f"Parsed registers for {name}: {list(ip_info['registers'].keys())}")
            
            ip_dict[name] = ip_info
            
        return ip_dict
        
    def _create_ip_attributes(self):
        """Crea attributi per accesso diretto agli IP"""
        for name, ip_info in self._ip_dict.items():
            # Usa IPCore invece di MMIO diretto
            ip_core = IPCore(
                base_addr=ip_info['phys_addr'],
                length=ip_info['addr_range'],
                ip_name=name,
                overlay_id=self._overlay_id,
                registers=ip_info.get('registers')
            )
            
            setattr(self, name, ip_core)
            
            if ip_info.get('registers'):
                logger.info(f"Created {name} with register_map containing {len(ip_info['registers'])} registers")

    @property
    def ip_dict(self):
        """Ritorna dizionario degli IP cores"""
        return self._ip_dict
        
    @property
    def bitfile_name(self):
        """Ritorna nome del bitfile"""
        return self._bitfile_name
    
    def __repr__(self):
        """Mostra info overlay"""
        output = f"Overlay: {self._bitfile_name}\n"
        output += f"IP Cores:\n"
        for name in sorted(self._ip_dict.keys()):
            ip_info = self._ip_dict[name]
            output += f"  {name}: {ip_info['type']} @ 0x{ip_info['phys_addr']:08X}\n"
        return output
    
    def close(self):
        """Chiude overlay e pulisce risorse associate"""
        if not self._closed:
            logger.info(f"Closing overlay {self._bitfile_name}")
            # Il cleanup generale avverrà tramite Connection
            self._closed = True
    
    def __del__(self):
        """Distruttore - assicura cleanup"""
        if hasattr(self, '_closed') and not self._closed:
            self.close()
    
    def __enter__(self):
        """Context manager entry"""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()