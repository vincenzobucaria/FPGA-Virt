#!/bin/bash
# run_server.sh - Fixed version

# Esegui il server con PYNQ reale
export USE_REAL_PYNQ=true
export CONFIG_PATH=config.yaml
export PYNQ_DEBUG_MODE=false
# Assicurati di essere root per accedere all'hardware
if [ "$EUID" -ne 0 ]; then 
    echo "Per accedere all'hardware PYNQ serve eseguire come root"
    echo "Usa: sudo -E $0"
    exit 1
fi

# Controlla che siamo su una board PYNQ
if [ ! -d "/usr/local/share/pynq-venv" ]; then
    echo "Warning: Non sembra essere una board PYNQ standard"
    echo "Continuo comunque..."
fi

echo "Starting PYNQ multi-tenant server with REAL hardware..."
echo "=================================================="
echo "Mode: REAL PYNQ"
echo "Config: $CONFIG_PATH"
echo "Current directory: $(pwd)"
echo ""

# NON cambiare directory se sei già in Hypervisor
# Avvia il server direttamente
python3 server.py --real-pynq