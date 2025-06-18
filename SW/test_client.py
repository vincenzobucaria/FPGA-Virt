#!/usr/bin/env python3
# test_register_mmio_simple.py

import os
import sys
import time
# Setup
os.environ['TENANT_ID'] = 'tenant1'
os.environ['PYNQ_API_KEY'] = 'test_key_1'

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from client.pynq_proxy import Overlay, allocate


print("=== Simple Register Map vs MMIO Test ===\n")
    
# 1. Load overlay
print("1. Loading overlay...")
overlay = Overlay('vector_sum.bit')
print("✅ Overlay loaded\n")
    
vector_sum = overlay.vector_scalar_add_0
my_ip = overlay.vector_scalar_add_0
    
    # Ottieni informazioni usando mmio

input_vector = allocate(shape=(1024,), dtype='int32')    
output_vector = allocate(shape=(1024,), dtype='int32')

input_vector[:] = range(1024)
print(input_vector[:])   
print(vector_sum.register_map)
vector_sum.register_map.input_vector_1 = input_vector.physical_address
vector_sum.register_map.output_vector_1 = output_vector.physical_address
input_vector.sync_to_device()
vector_sum.register_map.size = 1023
vector_sum.register_map.scalar = 11
vector_sum.write(0x000, 1)
time.sleep(1)
print("OUTPUT: ", output_vector[:])

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

