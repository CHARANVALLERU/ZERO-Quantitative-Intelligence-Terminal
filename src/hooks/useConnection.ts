'use client';

import { useState, useEffect } from 'react';
import { getSocket } from '@/lib/api';

// Connection status types
type ConnectionStatus = 'connecting' | 'connected' | 'disconnected' | 'error';

export function useConnectionStatus(): ConnectionStatus {
  const [status, setStatus] = useState<ConnectionStatus>('connecting');

  useEffect(() => {
    const socket = getSocket();
    
    const onConnect = () => setStatus('connected');
    const onDisconnect = () => setStatus('disconnected');
    const onError = () => setStatus('error');

    socket.on('connect', onConnect);
    socket.on('disconnect', onDisconnect);
    socket.on('connect_error', onError);

    // Initial status
    if (socket.connected) {
      setStatus('connected');
    } else {
      setStatus('connecting');
    }

    return () => {
      socket.off('connect', onConnect);
      socket.off('disconnect', onDisconnect);
      socket.off('connect_error', onError);
    };
  }, []);

  return status;
}

export function ConnectionStatusBadge() {
  const status = useConnectionStatus();
  const config = {
    connecting: { color: 'bg-yellow-500', text: 'Connecting...' },
    connected: { color: 'bg-green-500', text: 'Connected' },
    disconnected: { color: 'bg-red-500', text: 'Disconnected' },
    error: { color: 'bg-red-700', text: 'Error' },
  };

  const configItem = config[status] || config.connecting;

  return (
    <div className="fixed bottom-4 right-4 z-50 flex items-center gap-2 px-3 py-2 rounded-lg bg-gray-900 border border-gray-700 shadow-lg">
      <span className={`inline-block w-3 h-3 rounded-full ${configItem.color}`} />
      <span className="text-sm text-gray-300 font-medium">{configItem.text}</span>
    </div>
  );
}
