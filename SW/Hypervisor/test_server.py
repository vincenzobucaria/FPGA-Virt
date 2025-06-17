#!/usr/bin/env python3
# test_basic.py

import grpc
import sys
import os

# Aggiungi path per i proto
sys.path.append(os.path.join(os.path.dirname(__file__), 'Proto', 'generated'))
sys.path.append('../Proto/generated')
import pynq_service_pb2 as pb2
import pynq_service_pb2_grpc as pb2_grpc

def test_basic():
    print("=== PYNQ Multi-tenant Test ===\n")
    
    # 1. Connetti al server
    print("1. Connecting to server...")
    channel = grpc.insecure_channel('unix:///tmp/pynq_sockets/tenant1.sock')  # Porta TCP in debug mode
    stub = pb2_grpc.PYNQServiceStub(channel)
    print("✅ Connected\n")
    
    # 2. Test autenticazione
    print("2. Testing authentication...")
    auth_req = pb2.AuthRequest(
        tenant_id='tenant1',
        api_key='test_key_1'
    )
    
    try:
        auth_resp = stub.Authenticate(auth_req)
        print(f"✅ Authentication: {'SUCCESS' if auth_resp.success else 'FAILED'}")
        print(f"   Token: {auth_resp.session_token[:20]}...")
        print(f"   Message: {auth_resp.message}\n")
        
        if not auth_resp.success:
            print("❌ Authentication failed!")
            return
            
        token = auth_resp.session_token
        metadata = [('auth-token', token)]
        
    except grpc.RpcError as e:
        print(f"❌ Error: {e.details()}")
        return
    
    # 3. Test caricamento overlay
    print("3. Testing overlay load...")
    overlay_req = pb2.LoadOverlayRequest(
        bitfile_path='base.bit',
        download=True
    )
    
    try:
        overlay_resp = stub.LoadOverlay(overlay_req, metadata=metadata)
        print(f"✅ Overlay loaded")
        print(f"   ID: {overlay_resp.overlay_id}")
        print(f"   IP Cores found: {len(overlay_resp.ip_cores)}")
        
        for name, ip_core in overlay_resp.ip_cores.items():
            print(f"   - {name}: 0x{ip_core.base_address:08X} ({ip_core.type})")
        print()
        
        overlay_id = overlay_resp.overlay_id
        
    except grpc.RpcError as e:
        print(f"❌ Error: {e.details()}")
        return
    
    # 4. Test creazione MMIO
    print("4. Testing MMIO creation...")
    
    # Prendi il primo IP core
    first_ip = list(overlay_resp.ip_cores.values())[0]
    first_ip_name = list(overlay_resp.ip_cores.keys())[0]
    
    mmio_req = pb2.CreateMMIORequest(
        overlay_id=overlay_id,
        ip_name=first_ip_name,
        base_address=first_ip.base_address,
        length=first_ip.address_range
    )
    
    try:
        mmio_resp = stub.CreateMMIO(mmio_req, metadata=metadata)
        print(f"✅ MMIO created")
        print(f"   Handle: {mmio_resp.handle}\n")
        
        mmio_handle = mmio_resp.handle
        
    except grpc.RpcError as e:
        print(f"❌ Error: {e.details()}")
        return
    
    # 5. Test MMIO write/read
    print("5. Testing MMIO write/read...")
    
    # Write
    write_req = pb2.MMIOWriteRequest(
        handle=mmio_handle,
        offset=0,
        value=0xDEADBEEF
    )
    
    try:
        stub.MMIOWrite(write_req, metadata=metadata)
        print(f"✅ MMIO Write: 0xDEADBEEF at offset 0")
        
    except grpc.RpcError as e:
        print(f"❌ Write error: {e.details()}")
        return
    
    # Read
    read_req = pb2.MMIOReadRequest(
        handle=mmio_handle,
        offset=0,
        length=4
    )
    
    try:
        read_resp = stub.MMIORead(read_req, metadata=metadata)
        print(f"✅ MMIO Read: 0x{read_resp.value:08X}")
        
        if read_resp.value == 0xDEADBEEF:
            print("   ✅ Read value matches written value!\n")
        else:
            print("   ❌ Read value doesn't match!\n")
            
    except grpc.RpcError as e:
        print(f"❌ Read error: {e.details()}")
        return
    
    # 6. Test buffer allocation
    print("6. Testing buffer allocation...")
    
    buffer_req = pb2.AllocateBufferRequest(
        size=1024,
        buffer_type=0,  # Normal buffer
        name="test_buffer"
    )
    
    try:
        buffer_resp = stub.AllocateBuffer(buffer_req, metadata=metadata)
        print(f"✅ Buffer allocated")
        print(f"   Handle: {buffer_resp.handle}")
        print(f"   Physical Address: 0x{buffer_resp.physical_address:08X}")
        print(f"   Size: {buffer_resp.size} bytes\n")
        
    except grpc.RpcError as e:
        print(f"❌ Error: {e.details()}")
    
    print("=== Test completed! ===")

if __name__ == '__main__':
    test_basic()