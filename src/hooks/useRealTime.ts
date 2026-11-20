'use client';

import { useState, useCallback } from 'react';
import { subscriptionManager } from '@/lib/api';

// Real-time data hook for any subscribable channel
export function useRealTimeData<T>(
  channel: string,
  initialData: T,
  dependencies: any[] = []
): { data: T; updateData: (newData: T) => void } {
  const [data, setData] = useState<T>(initialData);

  const updateData = useCallback((newData: T) => {
    setData(newData);
    // Also broadcast via subscription manager
    subscriptionManager.broadcast(channel, newData);
  }, [channel]);

  useSubscription(channel, (newData: T) => {
    setData(newData);
  }, dependencies);

  return { data, updateData };
}

// Generic subscription hook
export function useSubscription<T>(
  channel: string,
  callback: (data: T) => void,
  dependencies: any[] = []
): void {
  const stableCallback = useCallback(
    (data: T) => {
      try {
        callback(data);
      } catch (error) {
        console.error(`Error in subscription callback for ${channel}:`, error);
      }
    },
    dependencies
  );

  useEffect(() => {
    subscriptionManager.subscribe(channel, stableCallback);

    return () => {
      subscriptionManager.unsubscribe(channel, stableCallback);
    };
  }, [channel, stableCallback]);
}

// Market data hook with auto-refresh
export function useMarketData(refreshInterval: number = 5000) {
  const [data, setData] = useState<any>({});
  const [isLive, setIsLive] = useState(false);

  useEffect(() => {
    // Fetch initial data
    const fetchData = async () => {
      try {
        const response = await fetch('/api/market-data');
        if (response.ok) {
          const result = await response.json();
          setData(result);
          setIsLive(true);
        }
      } catch (error) {
        console.error('Failed to fetch market data:', error);
      }
    };

    fetchData();
    const interval = setInterval(fetchData, refreshInterval);

    return () => clearInterval(interval);
  }, [refreshInterval]);

  return { data, isLive };
}

// Optimized data fetching with caching
export async function fetchWithCache<T>(
  url: string,
  cacheKey: string,
  ttl: number = 60
): Promise<T> {
  // Check cache first
  try {
    const cached = sessionStorage.getItem(cacheKey);
    if (cached) {
      const { data, timestamp } = JSON.parse(cached);
      if (Date.now() - timestamp < ttl * 1000) {
        return data as T;
      }
    }
  } catch (error) {
    console.error('Cache read error:', error);
  }

  // Fetch from API
  const response = await fetch(url);
  if (!response.ok) {
    throw new Error(`API error: ${response.status}`);
  }

  const data = await response.json();

  // Store in cache
  try {
    sessionStorage.setItem(cacheKey, JSON.stringify({
      data,
      timestamp: Date.now(),
    }));
  } catch (error) {
    console.error('Cache write error:', error);
  }

  return data as T;
}
