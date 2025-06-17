#!/usr/bin/env python3
# scripts/generate_proto.py

import os
import sys
from pathlib import Path
from grpc_tools import protoc

def generate_proto():
    """Genera file Python dai proto"""
    
    # Directory
    root_dir = Path(__file__).parent
    proto_dir = root_dir
    out_dir = root_dir / "generated"
    
    # Crea directory output
    out_dir.mkdir(exist_ok=True)
    
    # Trova tutti i file .proto
    proto_files = list(proto_dir.glob("*.proto"))
    
    if not proto_files:
        print("❌ Nessun file .proto trovato!")
        return False
    
    # Genera per ogni proto
    for proto_file in proto_files:
        print(f"Generando {proto_file.name}...")
        
        # Chiama protoc
        result = protoc.main([
            'protoc',
            f'-I{proto_dir}',
            f'--python_out={out_dir}',
            f'--grpc_python_out={out_dir}',
            str(proto_file)
        ])
        
        if result != 0:
            print(f"❌ Errore generando {proto_file.name}")
            return False
    
    # Crea __init__.py
    (out_dir / "__init__.py").touch()
    
    print("✅ Generazione completata!")
    return True

if __name__ == "__main__":
    sys.exit(0 if generate_proto() else 1)