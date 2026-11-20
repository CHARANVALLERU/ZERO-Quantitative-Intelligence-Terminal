'use client';

import { usePredictions, PredictionData } from '@/hooks/useApi';
import { ConnectionStatusBadge } from '@/hooks/useConnection';
import { useState } from 'react';

// Hero component with real-time clock
function HeroClock() {
  const [time, setTime] = useState(new Date());

  setInterval(() => {
    setTime(new Date());
  }, 1000);

  return (
    <div className="text-center">
      <div className="text-6xl font-bold text-white font-mono tracking-wider">
        {time.toLocaleTimeString('en-IN', { timeZone: 'Asia/Kolkata' })}
      </div>
      <div className="text-lg text-gray-400 mt-2 tracking-widest">
        {time.toLocaleDateString('en-IN', {
          timeZone: 'Asia/Kolkata',
          weekday: 'long',
          year: 'numeric',
          month: 'long',
          day: 'numeric'
        })}
      </div>
    </div>
  );
}

// Market prediction card with animated values
export default function PredictionCard({ index }: { index: string }) {
  const { data, loading, error } = usePredictions(index);
  
  if (loading) {
    return (
      <div className="rounded-xl bg-gray-900/50 border border-gray-700 p-6 animate-pulse">
        <div className="h-8 bg-gray-700 rounded mb-4"></div>
        <div className="space-y-4">
          <div className="h-12 bg-gray-800 rounded"></div>
          <div className="h-12 bg-gray-800 rounded"></div>
          <div className="h-12 bg-gray-800 rounded"></div>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl bg-gray-900/50 border border-red-500/50 p-6">
        <h3 className="text-red-400 text-lg font-semibold mb-2">Error</h3>
        <p className="text-red-300">{error}</p>
      </div>
    );
  }

  const d = data?.data;
  if (!d) return null;

  // Calculate percentage changes
  const openChange = ((d.pred_open - d.prev_close) / d.prev_close) * 100;
  const highChange = ((d.pred_high - d.prev_close) / d.prev_close) * 100;
  const lowChange = ((d.pred_low - d.prev_close) / d.prev_close) * 100;

  return (
    <div className="rounded-xl bg-gray-900/50 border border-gray-700 p-6 hover:border-gray-500 transition-colors duration-300">
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h2 className="text-2xl font-bold text-white">{index}</h2>
          <p className="text-sm text-gray-400">Prediction Vector</p>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-block w-2 h-2 rounded-full bg-green-500 animate-pulse"></span>
          <span className="text-xs text-gray-500">LIVE</span>
        </div>
      </div>

      {/* Key Metrics */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div className="bg-gray-800/50 rounded-lg p-3">
          <p className="text-xs text-gray-400 uppercase tracking-wider">Opening</p>
          <p className="text-xl font-semibold text-white">
            {d.pred_open?.toLocaleString('en-IN') || '—'}
            <span className={`ml-2 text-xs ${openChange >= 0 ? 'text-green-400' : 'text-red-400'}`}>
              {openChange >= 0 ? '+' : ''}{openChange.toFixed(2)}%
            </span>
          </p>
        </div>
        <div className="bg-gray-800/50 rounded-lg p-3">
          <p className="text-xs text-gray-400 uppercase tracking-wider">Previous Close</p>
          <p className="text-xl font-semibold text-white">
            {d.prev_close?.toLocaleString('en-IN') || '—'}
          </p>
        </div>
        <div className="bg-gray-800/50 rounded-lg p-3">
          <p className="text-xs text-gray-400 uppercase tracking-wider">High</p>
          <p className="text-xl font-semibold text-green-400">
            {d.pred_high?.toLocaleString('en-IN') || '—'}
            <span className="ml-2 text-xs text-gray-500">+{highChange.toFixed(2)}%</span>
          </p>
        </div>
        <div className="bg-gray-800/50 rounded-lg p-3">
          <p className="text-xs text-gray-400 uppercase tracking-wider">Low</p>
          <p className="text-xl font-semibold text-red-400">
            {d.pred_low?.toLocaleString('en-IN') || '—'}
            <span className="ml-2 text-xs text-gray-500">{lowChange.toFixed(2)}%</span>
          </p>
        </div>
      </div>

      {/* Sentiment & Confidence */}
      <div className="grid grid-cols-2 gap-4">
        <div className="flex items-center gap-2">
          <span className="text-sm text-gray-400">Sentiment:</span>
          <span className={`text-sm font-semibold ${
            (d.sentiment_score || 0) > 0.2 ? 'text-green-400' : 
            (d.sentiment_score || 0) < -0.2 ? 'text-red-400' : 'text-gray-300'
          }`}>
            {d.sentiment_score?.toFixed(2) || '—'}
          </span>
          <span className="text-xs text-gray-500">({d.sentiment_intensity || 'neutral'})</span>
        </div>
        <div className="flex items-center justify-end gap-2">
          <span className="text-sm text-gray-400">Confidence:</span>
          <span className="text-sm font-semibold text-yellow-400">
            {(d.confidence || 0) * 100}%
          </span>
        </div>
      </div>

      {/* Direction */}
      <div className="mt-4">
        <span className={`inline-block px-3 py-1 rounded-full text-sm font-semibold ${
          (d.movement_side?.includes('Bullish')) ? 'bg-green-500/20 text-green-400' :
          (d.movement_side?.includes('Bearish')) ? 'bg-red-500/20 text-red-400' :
          'bg-gray-500/20 text-gray-400'
        }`}>
          {d.movement_side || 'Neutral'}
        </span>
      </div>

      <ConnectionStatusBadge />
    </div>
  );
}

export default function PredictionsPage() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="container mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8">
          <HeroClock />
        </div>

        {/* Main Content */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          {/* NIFTY 50 */}
          <div className="lg:col-span-1">
            <PredictionCard index="NIFTY 50" />
          </div>

          {/* BANKNIFTY */}
          <div className="lg:col-span-1">
            <PredictionCard index="BANKNIFTY" />
          </div>

          {/* SENSEX */}
          <div className="lg:col-span-1">
            <PredictionCard index="SENSEX" />
          </div>
        </div>

        {/* Inter-market analysis */}
        <div className="mt-8 grid grid-cols-1 md:grid-cols-3 gap-6">
          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6">
            <h3 className="text-lg font-semibold text-white mb-4">Inter-Market Signals</h3>
            <div className="space-y-4">
              <div>
                <p className="text-sm text-gray-400">S&P 500 Change</p>
                <p className="text-xl font-semibold text-green-400">+0.45%</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">Crude Oil Change</p>
                <p className="text-xl font-semibold text-red-400">-1.23%</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">DXY Change</p>
                <p className="text-xl font-semibold text-green-400">+0.18%</p>
              </div>
            </div>
          </div>

          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6">
            <h3 className="text-lg font-semibold text-white mb-4">Options Data</h3>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-sm text-gray-400">PCR</p>
                <p className="text-xl font-semibold text-yellow-400">1.15</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">VIX</p>
                <p className="text-xl font-semibold text-orange-400">18.5</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">ATM Strike</p>
                <p className="text-xl font-semibold text-blue-400">24,800</p>
              </div>
              <div>
                <p className="text-sm text-gray-400">OI Change</p>
                <p className="text-xl font-semibold text-purple-400">+2.3%</p>
              </div>
            </div>
          </div>

          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6">
            <h3 className="text-lg font-semibold text-white mb-4">News Impact</h3>
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <span className="text-green-400">●</span>
                <span className="text-sm text-gray-300">High impact news</span>
                <span className="text-xs text-gray-500 ml-auto">3</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-yellow-400">●</span>
                <span className="text-sm text-gray-300">Medium impact</span>
                <span className="text-xs text-gray-500 ml-auto">5</span>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-red-400">●</span>
                <span className="text-sm text-gray-300">Low impact</span>
                <span className="text-xs text-gray-500 ml-auto">12</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
