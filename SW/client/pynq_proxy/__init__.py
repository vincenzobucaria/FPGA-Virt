# client/pynq_proxy/__init__.py
"""
PYNQ Proxy Client - Drop-in replacement for PYNQ in containers
"""

from .overlay import Overlay
from .mmio import MMIO
from .allocate import allocate, ProxyBuffer
from .fast_mmio import FastMMIO, UltraFastMMIO
# Esporta API compatibile con PYNQ
__all__ = ['Overlay', 'MMIO', 'allocate']

# Versione
__version__ = '0.1.0'