import { useEffect, useState, useCallback } from 'react';
import { fetchApi, subscriptionManager } from '@/lib/api';
import { getSocket } from '@/lib/api';

export interface PredictionData {
  index: string;
  data: {
    prev_close: number;
    pred_open: number;
    pred_high: number;
    pred_low: number;
    pred_close: number;
    confidence: number;
    movement_side: string;
    [key: string]: any;
  };
}

export interface LivePrice {
  price: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
  source: string;
}

export interface NewsItem {
  id: string;
  title: string;
  link: string;
  source: string;
  published: string;
  direction: string;
  impact_score: number;
  category_label: string;
  [key: string]: any;
}

export interface BreakingNews {
  breaking: NewsItem[];
  feed: NewsItem[];
  timestamp: string;
}

// Cache configuration
const CACHE_TTLS = {
  predictions: 60, // 60 seconds
  prices: 10, // 10 seconds for live prices
  news: 120, // 2 minutes for news
};

// Data fetcher hooks
export function usePredictions(index: string = 'NIFTY 50') {
  const [data, setData] = useState<PredictionData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchPredictions = useCallback(async () => {
    setLoading(true);
    try {
      const result = await fetchApi<PredictionData>(`/api/predictions/${index}`, {
        key: `predictions_${index}`,
        ttl: CACHE_TTLS.predictions,
      });
      setData(result);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [index]);

  useEffect(() => {
    fetchPredictions();

    // Subscribe to real-time updates
    const socket = getSocket();
    if (socket.connected) {
      socket.emit('subscribe', 'predictions');
      socket.on('predictions', (newData: PredictionData) => {
        if (newData.index === index) {
          setData(newData);
        }
      });
    }

    return () => {
      socket.off('predictions');
    };
  }, [fetchPredictions, index]);

  return { data, loading, error };
}

export function useLivePrices() {
  const [prices, setPrices] = useState<Record<string, LivePrice>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);

  const fetchPrices = useCallback(async () => {
    try {
      const result = await fetchApi<{ prices: Record<string, LivePrice> }>('/api/live-prices', {
        key: 'live_prices',
        ttl: CACHE_TTLS.prices,
      });
      setPrices(result.prices);
      setLastUpdated(new Date());
      setError(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPrices();

    // Set up interval for live price refresh
    const interval = setInterval(fetchPrices, 5000);
    
    // Subscribe to real-time updates via socket
    const socket = getSocket();
    if (socket.connected) {
      socket.emit('subscribe', 'prices');
      socket.on('prices', (priceData: Record<string, LivePrice>) => {
        setPrices(priceData);
        setLastUpdated(new Date());
      });
    }

    return () => {
      clearInterval(interval);
      socket.off('prices');
    };
  }, [fetchPrices]);

  return { prices, loading, error, lastUpdated };
}

export function useNews(limit: number = 20) {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [breaking, setBreaking] = useState<NewsItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const fetchNews = useCallback(async () => {
    try {
      const result = await fetchApi<{ news: NewsItem[]; breaking: NewsItem[] }>(`/api/news?limit=${limit}`, {
        key: 'news',
        ttl: CACHE_TTLS.news,
      });
      setNews(result.news);
      setBreaking(result.breaking);
      setError(null);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [limit]);

  useEffect(() => {
    fetchNews();

    // Set up interval for news refresh
    const interval = setInterval(fetchNews, 30000);

    // Subscribe to real-time updates
    const socket = getSocket();
    if (socket.connected) {
      socket.emit('subscribe', 'news');
      socket.on('news', (newsData: { news: NewsItem[]; breaking: NewsItem[] }) => {
        setNews(newsData.news);
        setBreaking(newsData.breaking);
      });
    }

    return () => {
      clearInterval(interval);
      socket.off('news');
    };
  }, [fetchNews]);

  return { news, breaking, loading, error };
}

// Utility hooks
export function useInterval(callback: () => void, delay: number) {
  const savedCallback = useRef(callback);

  useEffect(() => {
    savedCallback.current = callback;
  }, [callback]);

  useEffect(() => {
    if (delay === null) return;

    const id = setInterval(() => savedCallback.current(), delay);
    return () => clearInterval(id);
  }, [delay]);
}

export function useLocalStorage<T>(key: string, initialValue: T): [T, (value: T | ((prev: T) => T)) => void] {
  const [storedValue, setStoredValue] = useState<T>(() => {
    try {
      const item = window.localStorage.getItem(key);
      return item ? JSON.parse(item) : initialValue;
    } catch (error) {
      console.error(error);
      return initialValue;
    }
  });

  const setValue = (value: T | ((prev: T) => T)) => {
    setStoredValue(prev => {
      const valueToStore = value instanceof Function ? value(prev) : value;
      try {
        window.localStorage.setItem(key, JSON.stringify(valueToStore));
      } catch (error) {
        console.error(error);
      }
      return valueToStore;
    });
  };

  return [storedValue, setValue];
}
