# hypervisor/server.py
#!/usr/bin/env python3

import os
import sys
import grpc
import signal
import logging
import argparse
from concurrent import futures
from pathlib import Path
os.environ['PYNQ_DEBUG_MODE'] = 'true'
from mock_resource_manager import MockResourceManager as ResourceManager
print("[DEBUG] Using MockResourceManager, DEBUG MODE ON!")

# Import nostri moduli

from tenant_manager import TenantManager, TenantResources
from servicer import PYNQServicer
import time  # Aggiungi questo
from config_manager import DynamicConfigManager  # Aggiungi questo
from management_service import ManagementServicer  # Aggiungi questo

# Aggiungi anche l'import per il management service proto
import pynq_service_pb2_grpc as pb2_grpc
# Import generated proto
sys.path.append('./generated')
import pynq_service_pb2_grpc as pb2_grpc
DEBUG_MODE = True




# Import management service se presente
try:
    from management_service import ManagementServicer
    MANAGEMENT_ENABLED = True
except ImportError:
    print("Warning: Management service not available")
    MANAGEMENT_ENABLED = False


# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class PYNQMultiTenantServer:
    def __init__(self, config_file: str = None):
        logger.info("Initializing PYNQ Multi-Tenant Server")
        
        # Carica configurazione
        self.config_manager = DynamicConfigManager(config_file)
        self.config_manager.register_watcher(self._on_config_change)
        # Inizializza managers
        self.tenant_manager = TenantManager(self.config_manager.tenants)
        self.resource_manager = ResourceManager(self.tenant_manager)

       
        self.resource_manager = ResourceManager(self.tenant_manager)
        # Server gRPC per tenant
        self.servers = {}
        self.management_server = None  # Aggiungi questo
        # Setup signal handlers
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)
    
    def _create_tenant_server(self, tenant_id: str) -> grpc.Server:
        """Crea server gRPC per un tenant"""
        tenant_config = self.config_manager.tenants[tenant_id]
        
        # Socket path
        socket_path = os.path.join(self.config_manager.socket_dir, f"{tenant_id}.sock")
        
        # Rimuovi socket esistente
        if os.path.exists(socket_path):
            os.unlink(socket_path)
        
        # Crea server
        server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=10),
            options=[
                ('grpc.max_send_message_length', 100 * 1024 * 1024),
                ('grpc.max_receive_message_length', 100 * 1024 * 1024),
            ]
        )
        
        # Aggiungi servicer
        servicer = PYNQServicer(self.tenant_manager, self.resource_manager)
        pb2_grpc.add_PYNQServiceServicer_to_server(servicer, server)
        
        # Bind a Unix socket
        server.add_insecure_port(f'unix://{socket_path}')
        
        # Set permissions
        os.chmod(socket_path, 0o600)
        os.chown(socket_path, tenant_config.uid, tenant_config.gid)
        
        logger.info(f"Created server for {tenant_id} on {socket_path}")
        
        return server
    
    def start(self):
        """Avvia tutti i server"""
        logger.info("Starting PYNQ Multi-Tenant Server")
        
        # Crea directory socket
        os.makedirs(self.config_manager.socket_dir, exist_ok=True)
        self._start_management_server()
        # Avvia server per ogni tenant
        for tenant_id in self.config_manager.tenants:
            server = self._create_tenant_server(tenant_id)
            server.start()
            self.servers[tenant_id] = server
            logger.info(f"Started server for tenant {tenant_id}")
        
        logger.info(f"All servers started. Total tenants: {len(self.servers)}")
        
        # Wait forever
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        """Ferma tutti i server"""
        logger.info("Stopping servers...")
        
        for tenant_id, server in self.servers.items():
            logger.info(f"Stopping server for {tenant_id}")
            server.stop(grace=5)
        
        logger.info("All servers stopped")
    
    def _handle_signal(self, signum, frame):
        """Gestisce segnali di terminazione"""
        logger.info(f"Received signal {signum}")
        self.stop()
        sys.exit(0)
    def _on_config_change(self, event_type: str, data):
        """Gestisce modifiche alla configurazione"""
        logger.info(f"Config change: {event_type} - {data}")
        
        if event_type == 'tenant_added':
            tenant_id = data
            # Aggiungi a tenant manager
            self.tenant_manager.config[tenant_id] = self.config_manager.tenants[tenant_id]
            self.tenant_manager.resources[tenant_id] = TenantResources()
            
        elif event_type == 'tenant_removed':
            tenant_id = data
            # Rimuovi da tenant manager
            if tenant_id in self.tenant_manager.config:
                del self.tenant_manager.config[tenant_id]
                del self.tenant_manager.resources[tenant_id]
                
        elif event_type == 'tenant_updated':
            tenant_id = data
            # Aggiorna tenant manager
            self.tenant_manager.config[tenant_id] = self.config_manager.tenants[tenant_id]

    def create_and_start_tenant_server(self, tenant_id: str):
        """Crea e avvia server per nuovo tenant"""
        if tenant_id in self.servers:
            raise Exception(f"Server for {tenant_id} already exists")
            
        server = self._create_tenant_server(tenant_id)
        server.start()
        self.servers[tenant_id] = server
        
        logger.info(f"Started new server for tenant {tenant_id}")

    def stop_tenant_server(self, tenant_id: str):
        """Ferma server di un tenant"""
        if tenant_id not in self.servers:
            return
            
        logger.info(f"Stopping server for tenant {tenant_id}")
        server = self.servers[tenant_id]
        server.stop(grace=5)
        del self.servers[tenant_id]
        
        # Rimuovi socket
        socket_path = os.path.join(self.config_manager.socket_dir, f"{tenant_id}.sock")
        if os.path.exists(socket_path):
            os.unlink(socket_path)    
    def _on_config_change(self, event_type: str, data):
        """Gestisce modifiche alla configurazione"""
        logger.info(f"Config change: {event_type} - {data}")
        
        if event_type == 'tenant_added':
            tenant_id = data
            # Aggiungi a tenant manager
            self.tenant_manager.config[tenant_id] = self.config_manager.tenants[tenant_id]
            self.tenant_manager.resources[tenant_id] = TenantResources()
            
        elif event_type == 'tenant_removed':
            tenant_id = data
            # Rimuovi da tenant manager
            if tenant_id in self.tenant_manager.config:
                del self.tenant_manager.config[tenant_id]
                del self.tenant_manager.resources[tenant_id]
                
        elif event_type == 'tenant_updated':
            tenant_id = data
            # Aggiorna tenant manager
            self.tenant_manager.config[tenant_id] = self.config_manager.tenants[tenant_id]

    def create_and_start_tenant_server(self, tenant_id: str):
        """Crea e avvia server per nuovo tenant"""
        if tenant_id in self.servers:
            raise Exception(f"Server for {tenant_id} already exists")
            
        server = self._create_tenant_server(tenant_id)
        server.start()
        self.servers[tenant_id] = server
        
        logger.info(f"Started new server for tenant {tenant_id}")

    def stop_tenant_server(self, tenant_id: str):
        """Ferma server di un tenant"""
        if tenant_id not in self.servers:
            return
            
        logger.info(f"Stopping server for tenant {tenant_id}")
        server = self.servers[tenant_id]
        server.stop(grace=5)
        del self.servers[tenant_id]
        
        # Rimuovi socket
        socket_path = os.path.join(self.config_manager.socket_dir, f"{tenant_id}.sock")
        if os.path.exists(socket_path):
            os.unlink(socket_path)

    def _start_management_server(self):
        """Avvia server di management"""
        management_socket = os.path.join(self.config_manager.socket_dir, "management.sock")
        
        if os.path.exists(management_socket):
            os.unlink(management_socket)
        
        self.management_server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=5)
        )
        
        # Aggiungi management servicer
        management_servicer = ManagementServicer(self)
        pb2_grpc.add_PYNQManagementServiceServicer_to_server(
            management_servicer, 
            self.management_server
        )
        
        # Bind a Unix socket (solo root può accedere)
        self.management_server.add_insecure_port(f'unix://{management_socket}')
        
        # Permessi restrittivi
        os.chmod(management_socket, 0o600)  # Solo root
        
        self.management_server.start()
        logger.info(f"Management server started on {management_socket}")   

    def _start_management_server(self):
        """Avvia server di management"""
        management_socket = os.path.join(self.config_manager.socket_dir, "management.sock")
        
        if os.path.exists(management_socket):
            os.unlink(management_socket)
        
        self.management_server = grpc.server(
            futures.ThreadPoolExecutor(max_workers=5)
        )
        
        # Aggiungi management servicer
        management_servicer = ManagementServicer(self)
        pb2_grpc.add_PYNQManagementServiceServicer_to_server(
            management_servicer, 
            self.management_server
        )
        
        # Bind a Unix socket (solo root può accedere)
        self.management_server.add_insecure_port(f'unix://{management_socket}')
        
        # Permessi restrittivi
        os.chmod(management_socket, 0o600)  # Solo root
        
        self.management_server.start()
        logger.info(f"Management server started on {management_socket}")    

def main():
    parser = argparse.ArgumentParser(description='PYNQ Multi-Tenant Server')
    parser.add_argument(
        '-c', '--config',
        help='Configuration file path',
        default='config-dev.yaml' if DEBUG_MODE else '/etc/pynq/config.yaml'
    )
    parser.add_argument(
        '-d', '--debug',
        action='store_true',
        help='Enable debug logging'
    )
    
    args = parser.parse_args()
    
    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)
    
    # Check if running as root (necessario per PYNQ)
    if os.geteuid() != 0:
        logger.warning("Not running as root. Some operations may fail.")
    
    # Start server
    server = PYNQMultiTenantServer(args.config)
    server.start()

if __name__ == '__main__':
    main()