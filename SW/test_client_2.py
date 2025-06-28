#!/usr/bin/env python3
# test_register_mmio_simple.py

import os
import sys
import time
import numpy as np
# Setup
os.environ['TENANT_ID'] = 'tenant2'
os.environ['PYNQ_API_KEY'] = 'test_key_2'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client.pynq_proxy import Overlay, allocate, MMIO


print("=== Simple Register Map vs MMIO Test ===\n")

# 1. Load overlay
print("1. Loading overlay...")
overlay = Overlay('sub')
print("✅ Overlay loaded\n")
    

    
    # Ottieni informazioni usando mmio

size = 10

in_buf  = allocate(shape=(size,), dtype=np.int32)
out_buf = allocate(shape=(size,), dtype=np.int32)
PR_0_base_addr = 0xB0000000
MMIO_PR_0 = MMIO(PR_0_base_addr, 0x10000)
in_buf[:] = np.arange(size)
in_buf.sync_to_device()
print(f"\nInput buffer: {in_buf}")  
print(f"\nIndirizzi fisici:")
print(f"  Input: 0x{in_buf.physical_address:016X}")
print(f"  Output: 0x{out_buf.physical_address:016X}")

# Configura tutti i registri e verifica
print("\n=== CONFIGURAZIONE REGISTRI ===")

# Input pointer (64-bit)
MMIO_PR_0.write(0x10, in_buf.physical_address & 0xFFFFFFFF)
MMIO_PR_0.write(0x14, (in_buf.physical_address >> 32) & 0xFFFFFFFF)
print(f"INPUT_PTR: 0x{MMIO_PR_0.read(0x10):08X} 0x{MMIO_PR_0.read(0x14):08X}")

# Output pointer (64-bit)
MMIO_PR_0.write(0x1C, out_buf.physical_address & 0xFFFFFFFF)
MMIO_PR_0.write(0x20, (out_buf.physical_address >> 32) & 0xFFFFFFFF)
print(f"OUTPUT_PTR: 0x{MMIO_PR_0.read(0x1C):08X} 0x{MMIO_PR_0.read(0x20):08X}")

# Scalar
scalar_val = 20
MMIO_PR_0.write(0x28, scalar_val)
print(f"SCALAR: {MMIO_PR_0.read(0x28)}")

# Size
MMIO_PR_0.write(0x30, size)
print(f"SIZE: {MMIO_PR_0.read(0x30)}")

# Verifica ancora lo stato prima di partire
print("\n=== PRIMA DI START ===")
ctrl = MMIO_PR_0.read(0x00)
print(f"CTRL: 0x{ctrl:08X} (idle={ctrl & 0x04 != 0})")

# Avvia con AP_START
print("\n=== AVVIO ===")
MMIO_PR_0.write(0x00, 0x01)  # Solo AP_START

# Monitora lo stato con timeout
import time
timeout = 1.0  # 1 secondo di timeout
start_time = time.time()
count = 0

while True:
    ctrl = MMIO_PR_0.read(0x00)
    done = (ctrl >> 1) & 0x01
    idle = (ctrl >> 2) & 0x01
    
    if count % 1000 == 0:  # Stampa ogni 1000 iterazioni
        print(f"CTRL: 0x{ctrl:08X} - done={done}, idle={idle}")
    
    if done:
        print("✓ Completato!")
        break
        
    if time.time() - start_time > timeout:
        print("✗ Timeout!")
        print(f"Ultimo CTRL: 0x{ctrl:08X}")
        break
        
    count += 1

# Controlla il risultato

"""
print(vector_sum.register_map)
vector_sum.register_map.input_vector_1 = input_vector.physical_address
vector_sum.register_map.output_vector_1 = output_vector.physical_address
input_vector.sync_to_device()
vector_sum.register_map.size = 1023
vector_sum.register_map.scalar = 11
vector_sum.write(0x000, 1)
time.sleep(1)
"""
print("OUTPUT: ", out_buf[:])

print(r"""
 /\     /\
{  `---'  }
{  O   O  }
~~>  V  <~~
 \  \|/  /
  `-----'____
 /     \    \_
{       }\  )_\_   _
|  \_/  |/ /  \_\_/ )
 \__/  /(_/     \__/
   (__/
""")

