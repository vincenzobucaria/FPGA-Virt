#!/usr/bin/env python3
# test_register_mmio_simple.py

import os
import sys

# Setup
os.environ['TENANT_ID'] = 'tenant1'
os.environ['PYNQ_API_KEY'] = 'test_key_1'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client.pynq_proxy import Overlay

def test_simple():
    print("=== Simple Register Map vs MMIO Test ===\n")
    
    # 1. Load overlay
    print("1. Loading overlay...")
    overlay = Overlay('conv2d.bit')
    print("✅ Overlay loaded\n")
    
    # 2. Get conv2d IP
    print("2. Accessing conv2d IP...")
    conv2d = overlay.conv2d_0
    print(f"✅ Got conv2d at address 0x{conv2d.base_addr:08X}\n")
    
    # 3. Test writing via register_map, reading via MMIO
    print("3. Write via register_map, read via MMIO:")
    print("-" * 40)
    
    # Write some test values via register_map
    test_values = {
        'N': 2,
        'C_in': 3,
        'H_in': 224,
        'W_in': 224
    }
    
    for reg_name, value in test_values.items():
        # Write using register_map
        setattr(conv2d.register_map, reg_name, value)
        print(f"   Wrote {reg_name} = {value} via register_map")
        
        # Read back using direct MMIO
        offset = conv2d.register_map._registers[reg_name]['offset']
        read_value = conv2d.read(offset)
        
        if read_value == value:
            print(f"   ✅ Read back {read_value} via MMIO @ offset 0x{offset:04X}")
        else:
            print(f"   ❌ Read back {read_value} (expected {value})")
        print()
    
    # 4. Test writing via MMIO, reading via register_map
    print("\n4. Write via MMIO, read via register_map:")
    print("-" * 40)
    
    # Different test values
    test_values_2 = {
        'K_h': 5,
        'K_w': 5,
        'stride': 2,
        'padding': 1
    }
    
    for reg_name, value in test_values_2.items():
        # Get offset
        offset = conv2d.register_map._registers[reg_name]['offset']
        
        # Write using direct MMIO
        conv2d.write(offset, value)
        print(f"   Wrote {value} to offset 0x{offset:04X} via MMIO")
        
        # Read back using register_map
        read_value = getattr(conv2d.register_map, reg_name)
        
        if read_value == value:
            print(f"   ✅ Read back {reg_name} = {read_value} via register_map")
        else:
            print(f"   ❌ Read back {reg_name} = {read_value} (expected {value})")
        print()
    
    # 5. Show final register state
    print("\n5. Final register state:")
    print("-" * 40)
    print(conv2d.register_map)
    
    print("\n✅ Test completed!")

if __name__ == '__main__':
    try:
        test_simple()
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        import traceback
        traceback.prin