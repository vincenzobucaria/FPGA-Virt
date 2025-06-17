# client/connection.py
import os
import grpc
import logging
from typing import Optional, Dict, Any
import sys

# Import generated proto
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'Proto', 'generated'))
import pynq_service_pb2 as pb2
import pynq_service_pb2_grpc as pb2_grpc

logger = logging.getLogger(__name__)

class Connection:
    """Singleton connection manager per PYNQ proxy"""
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.tenant_id = os.environ.get('TENANT_ID', 'tenant1')
        self.channel = None
        self.stub = None
        self.token = None
        self._initialized = True
        
    def connect(self):
        """Stabilisce connessione con il server"""
        if self.channel:
            return
            
        # Determina come connettersi
        if os.environ.get('PYNQ_DEBUG_MODE', 'false').lower() == 'true':
            # Debug mode: usa TCP
            server_address = f"localhost:{50051}"
            self.channel = grpc.insecure_channel(server_address)
        else:
            # Production: usa Unix socket
            socket_path = f"/var/run/pynq/{self.tenant_id}.sock"
            self.channel = grpc.insecure_channel(f'unix://{socket_path}')
            
        self.stub = pb2_grpc.PYNQServiceStub(self.channel)
        
        # Autentica
        self._authenticate()
        
    def _authenticate(self):
        """Autentica con il server"""
        api_key = os.environ.get('PYNQ_API_KEY', 'test_key_1')
        
        request = pb2.AuthRequest(
            tenant_id=self.tenant_id,
            api_key=api_key
        )
        
        response = self.stub.Authenticate(request)
        
        if not response.success:
            raise Exception(f"Authentication failed: {response.message}")
            
        self.token = response.session_token
        logger.info(f"Authenticated as {self.tenant_id}")
        
    def call_with_auth(self, method_name: str, request):
        """Chiama metodo gRPC con autenticazione"""
        if not self.channel:
            self.connect()
            
        metadata = [('auth-token', self.token)]
        
        try:
            method = getattr(self.stub, method_name)
            return method(request, metadata=metadata)
        except grpc.RpcError as e:
            if e.code() == grpc.StatusCode.UNAUTHENTICATED:
                # Token scaduto, riautentica
                logger.info("Token expired, re-authenticating...")
                self._authenticate()
                # Riprova
                metadata = [('auth-token', self.token)]
                return method(request, metadata=metadata)
            raise