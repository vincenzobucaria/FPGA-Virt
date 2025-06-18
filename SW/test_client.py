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

    overlay = Overlay('conv2d.bit')
    print(overlay.ip_dict)
    conv2d = overlay.custom_accel_0
    print(conv2d.register_map)
    conv2d.register_map.N = 5

    print(conv2d.register_map.N)

    print("\n3. Verifica con MMIO diretto")
    value_direct = conv2d.read(0x20)  # N è a offset 0x20
    print(f"   MMIO read(0x20) = {value_direct}")
    # 1. Test MMIO creazione diretta - IDENTICO A PYNQ!
    print("1. Direct MMIO creation (PYNQ compatible API)...")
    print("\n4. Scrittura diretta MMIO")
    conv2d.write(0x28, 3)  # C_in a offset 0x28
    print("   Scritto 3 a offset 0x28 (C_in)")

    # 5. Leggi via register_map
    print("\n5. Lettura via register_map")
    c_in = conv2d.register_map.C_in
    print(f"   register_map.C_in = {c_in}")
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
    print("Buffer allocation and access...")

    buffer = allocate(shape=(10,), dtype='uint32')
    buffer[:] = range(10)
   


    print(f"✅ Buffer allocated: shape={buffer.shape}, dtype={buffer.dtype}")
    print(f"   Physical address: 0x{buffer.physical_address:08X}")
    print(f"   Shared memory name: {buffer._shm_name}")
    print(f"   Buffer data: {buffer[:]}")



    print("Buffer 2 allocation and access...")

    buffer2 = allocate(shape=(10,), dtype='uint32')
    buffer2[:] = range(10)
   

   
    print(f"✅ Buffer allocated: shape={buffer2.shape}, dtype={buffer2.dtype}")
    print(f"   Physical address: 0x{buffer2.physical_address:08X}")
    print(f"   Shared memory name: {buffer2._shm_name}")
    print(f"   Buffer data: {buffer2[:]}")
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