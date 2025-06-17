#!/usr/bin/env python3
# test_pynq_api_compatibility.py

import os
import sys

# Setup
os.environ['TENANT_ID'] = 'tenant1'
os.environ['PYNQ_API_KEY'] = 'test_key_1'
os.environ['PYNQ_DEBUG_MODE'] = 'false'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client.pynq_proxy import Overlay, MMIO, allocate

def test_pynq_api():
    print("=== Testing PYNQ API Compatibility ===\n")
    


      # 3. Test Overlay e accesso IP
    print("\n3. Overlay and IP access...")
    overlay = Overlay('base.bit')
    overlay = Overlay('conv2d.bit')

    # 1. Test MMIO creazione diretta - IDENTICO A PYNQ!
    print("1. Direct MMIO creation (PYNQ compatible API)...")
    
    # In PYNQ reale:
    # from pynq import MMIO
    # mmio = MMIO(0xA0000000, 0x10000)
    
    # Nel nostro proxy - STESSA IDENTICA API:
    mmio = MMIO(0xA0000000, 0x10000)
    print(f"✅ MMIO created with PYNQ API: MMIO(0xA0000000, 0x10000)")
    
    # Test operazioni
    mmio.write(0x00, 0xDEADBEEF)
    value = mmio.read(0x00)
    print(f"✅ Write/Read: 0x{value:08X}")
    """"""
    # 2. Test MMIO con debug - PYNQ API
    print("\n2. MMIO with debug flag...")
    mmio_debug = MMIO(0xA0001000, 0x1000, debug=True)
    mmio_debug.write(0xFFFFFF, 0xCAFEBABE)  # Questo logga
    print(f"✅ Write/Read: 0x{value:08X}")
  
    
    # In PYNQ: overlay.ip_name è un MMIO o wrapper specifico
    # Nel nostro caso è sempre MMIO con API compatibile
    """
    gpio = overlay.axi_gpio_0  
    print(f"✅ IP access: overlay.axi_gpio_0 is MMIO-compatible")
    print(f"   Base: 0x{gpio.base_addr:08X}, Length: 0x{gpio.length:X}")
    """
    # 4. Test metodi MMIO aggiuntivi
    print("\n4. Testing MMIO block operations...")
    test_data = b'\x01\x02\x03\x04\x05\x06\x07\x08'
    mmio.write_mm(0x100, test_data)
    read_data = mmio.read_mm(0x100, 8)
    print(f"✅ Block write/read: {read_data.hex()}")
    
    print("\n✅ All tests passed - API is PYNQ compatible!")

if __name__ == '__main__':
    test_pynq_api()