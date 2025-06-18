class RegisterMap:
    """Register map proxy per accesso named registers"""
    
    def __init__(self, mmio_proxy, register_dict=None):
        self._mmio = mmio_proxy
        self._registers = register_dict or {}
        
    def __setattr__(self, name, value):
        if name.startswith('_'):
            # Attributi interni
            super().__setattr__(name, value)
        elif hasattr(self._registers, name) or name in self._registers:
            # Scrittura registro
            if isinstance(self._registers, dict):
                offset = self._registers[name]['offset']
            else:
                # Potrebbe essere un oggetto con attributi
                offset = getattr(self._registers, name)
            self._mmio.write(offset, value)
        else:
            # Fallback: assume che il nome sia un offset numerico
            # o solleva eccezione
            raise AttributeError(f"No register named {name}")
    
    def __getattr__(self, name):
        if hasattr(self._registers, name) or name in self._registers:
            if isinstance(self._registers, dict):
                offset = self._registers[name]['offset']
            else:
                offset = getattr(self._registers, name)
            return self._mmio.read(offset)
        raise AttributeError(f"No register named {name}")