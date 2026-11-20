import { io, Socket } from 'socket.io-client';

// API configuration
const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

// Socket.IO configuration
const WS_BASE_URL = `${API_BASE_URL.replace('http://', 'ws://').replace('https://', 'wss://')}`;

// Initialize Socket.IO client
let socket: Socket | null = null;

export function getSocket(): Socket {
  if (!socket) {
    socket = io(WS_BASE_URL, {
      path: '/ws/market-data',
      transports: ['websocket', 'polling'],
      autoConnect: true,
      reconnection: true,
      reconnectionAttempts: Infinity,
      reconnectionDelay: 1000,
    });
  }
  return socket;
}

export function connectSocket(): Socket {
  const s = getSocket();
  if (!s.connected) {
    s.connect();
  }
  return s;
}

export function disconnectSocket(): void {
  if (socket) {
    socket.disconnect();
    socket = null;
  }
}

// API fetch function with caching
export async function fetchApi<T>(
  endpoint: string,
  cacheOptions?: { ttl?: number; key?: string }
): Promise<T> {
  const cacheKey = cacheOptions?.key || endpoint;
  const ttl = cacheOptions?.ttl || 60; // Default 60 seconds

  // Check cache first
  const cached = sessionStorage.getItem(cacheKey);
  if (cached) {
    const { data, timestamp } = JSON.parse(cached);
    if (Date.now() - timestamp < ttl * 1000) {
      return data as T;
    }
  }

  // Fetch from API
  const response = await fetch(`${API_BASE_URL}${endpoint}`);
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  const data = await response.json();

  // Cache the result
  if (cacheOptions?.key) {
    sessionStorage.setItem(cacheKey, JSON.stringify({
      data,
      timestamp: Date.now(),
    }));
  }

  return data as T;
}

// Subscription manager for real-time data
export class SubscriptionManager {
  private listeners: Map<string, ((data: any) => void)[]> = new Map();

  subscribe(channel: string, callback: (data: any) => void): void {
    if (!this.listeners.has(channel)) {
      this.listeners.set(channel, []);
    }
    this.listeners.get(channel)!.push(callback);

    // Auto-subscribe via socket if available
    if (typeof window !== 'undefined') {
      const socket = getSocket();
      if (socket.connected) {
        socket.emit('subscribe', channel);
      }
    }
  }

  unsubscribe(channel: string, callback: (data: any) => void): void {
    const listeners = this.listeners.get(channel);
    if (listeners) {
      const index = listeners.indexOf(callback);
      if (index > -1) {
        listeners.splice(index, 1);
      }
    }
  }

  unsubscribeAll(channel: string): void {
    this.listeners.delete(channel);
  }

  broadcast(channel: string, data: any): void {
    const listeners = this.listeners.get(channel);
    if (listeners) {
      listeners.forEach(callback => {
        try {
          callback(data);
        } catch (error) {
          console.error(`Error in listener for ${channel}:`, error);
        }
      });
    }
  }

  clearAll(): void {
    this.listeners.clear();
  }
}

// Global subscription instance
export const subscriptionManager = new SubscriptionManager();
