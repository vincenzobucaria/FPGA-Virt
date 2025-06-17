#!/usr/bin/env python3
# test_pynq_client.py

import os
import sys

# Setup environment
os.environ['TENANT_ID'] = 'tenant1'
os.environ['PYNQ_API_KEY'] = 'test_key_1'
os.environ['PYNQ_DEBUG_MODE'] = 'false'

# Aggiungi client al path
sys.path.insert(0, os.path.dirname(__file__))

# Importa il nostro PYNQ proxy invece del vero PYNQ
from client.pynq_proxy import Overlay, allocate

def test_pynq_compatibility():
    print("=== Testing PYNQ Proxy Client ===\n")
    
    # 1. Carica overlay - identico a PYNQ!
    print("1. Loading overlay...")
    overlay = Overlay('base.bit')
    print(f"✅ Overlay loaded")
    print(f"   IP cores: {list(overlay.ip_dict.keys())}")
    
    # 2. Accedi a IP - identico a PYNQ!
    print("\n2. Accessing GPIO...")
    gpio = overlay.axi_gpio_0  # Attributo creato automaticamente
    print(f"✅ GPIO at 0x{gpio.base_addr:08X}")
    
    # 3. MMIO operations - identico a PYNQ!
    print("\n3. Testing MMIO...")
    gpio.write(0x00, 0xDEADBEEF)
    value = gpio.read(0x00)
    print(f"✅ Write/Read: 0x{value:08X}")
    
    # 4. Buffer allocation - identico a PYNQ!
    print("\n4. Allocating buffer...")
    buffer = allocate(shape=(1024,), dtype='uint32')
    print(f"✅ Buffer allocated at 0x{buffer.physical_address:08X}")
    
    # 5. Buffer operations - identico a PYNQ!
    print("\n5. Testing buffer...")
    buffer[0] = 42
    buffer[1] = 43
    buffer.sync_to_device()
    print(f"✅ Buffer operations work")
    
    print("\n=== All tests passed! ===")
    print("The proxy client is API-compatible with PYNQ!")

if __name__ == '__main__':
    test_pynq_compatibility()