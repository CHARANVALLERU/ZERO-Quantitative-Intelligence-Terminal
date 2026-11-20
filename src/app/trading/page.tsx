'use client';

import { useLivePrices, LivePrice, usePredictions, PredictionData } from '@/hooks/useApi';
import { ConnectionStatusBadge } from '@/hooks/useConnection';
import { useState } from 'react';

// Price ticker component
export default function TradingPage() {
  const { prices, loading, error, lastUpdated } = useLivePrices();
  const { data: niftyData } = usePredictions('NIFTY 50');
  
  // Order placement state
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [quantity, setQuantity] = useState(1);
  const [limitPrice, setLimitPrice] = useState('');
  const [orderType, setOrderType] = useState<'MARKET' | 'LIMIT' | 'SL'>('MARKET');
  const [orderResult, setOrderResult] = useState<string | null>(null);

  const handleSubmitOrder = async () => {
    // Simulate order placement
    setOrderResult(`Order ${side} ${quantity} shares at ${limitPrice || 'market price'} - ${orderType} order placed successfully`);
    
    // Reset after 5 seconds
    setTimeout(() => setOrderResult(null), 5000);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
        <div className="animate-pulse text-gray-500">Loading market data...</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-400 text-lg">Error loading prices</p>
        </div>
      </div>
    );
  }

  const niftyPrice = prices['NIFTY 50'];
  const bankniftyPrice = prices['BANKNIFTY'];
  const sensexPrice = prices['SENSEX'];

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="container mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-4">Quantum Trading Terminal</h1>
          <p className="text-gray-400">Execute trades with intelligent risk management</p>
        </div>

        {/* Live Prices */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          {/* NIFTY 50 */}
          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6 hover:border-gray-500 transition-colors">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-blue-400 font-bold text-lg">NIFTY 50</span>
              <span className="text-xs text-gray-500 bg-gray-800 px-2 py-1 rounded">INDEX</span>
            </div>
            {niftyPrice?.price && (
              <div className="text-4xl font-bold text-white mb-2">
                {niftyPrice.price.toLocaleString('en-IN')}
                <span className="text-lg font-normal text-green-400 ml-2">
                  {(niftyPrice.price - niftyPrice.prev_close).toFixed(2)}
                </span>
              </div>
            )}
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-gray-400">Open</p>
                <p className="text-white font-semibold">{niftyPrice?.open?.toFixed(2) || '—'}</p>
              </div>
              <div>
                <p className="text-gray-400">High</p>
                <p className="text-white font-semibold">{niftyPrice?.high?.toFixed(2) || '—'}</p>
              </div>
              <div>
                <p className="text-gray-400">Low</p>
                <p className="text-white font-semibold">{niftyPrice?.low?.toFixed(2) || '—'}</p>
              </div>
            </div>
          </div>

          {/* BANKNIFTY */}
          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6 hover:border-gray-500 transition-colors">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-green-400 font-bold text-lg">BANKNIFTY</span>
              <span className="text-xs text-gray-500 bg-gray-800 px-2 py-1 rounded">INDEX</span>
            </div>
            {bankniftyPrice?.price && (
              <div className="text-4xl font-bold text-white mb-2">
                {bankniftyPrice.price.toLocaleString('en-IN')}
                <span className="text-lg font-normal text-green-400 ml-2">
                  {(bankniftyPrice.price - bankniftyPrice.prev_close).toFixed(2)}
                </span>
              </div>
            )}
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-gray-400">Open</p>
                <p className="text-white font-semibold">{bankniftyPrice?.open?.toFixed(2) || '—'}</p>
              </div>
              <div>
                <p className="text-gray-400">High</p>
                <p className="text-white font-semibold">{bankniftyPrice?.high?.toFixed(2) || '—'}</p>
              </div>
              <div>
                <p className="text-gray-400">Low</p>
                <p className="text-white font-semibold">{bankniftyPrice?.low?.toFixed(2) || '—'}</p>
              </div>
            </div>
          </div>

          {/* SENSEX */}
          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6 hover:border-gray-500 transition-colors">
            <div className="flex items-center gap-3 mb-4">
              <span className="text-yellow-400 font-bold text-lg">SENSEX</span>
              <span className="text-xs text-gray-500 bg-gray-800 px-2 py-1 rounded">INDEX</span>
            </div>
            {sensexPrice?.price && (
              <div className="text-4xl font-bold text-white mb-2">
                {sensexPrice.price.toLocaleString('en-IN')}
                <span className="text-lg font-normal text-green-400 ml-2">
                  {(sensexPrice.price - sensexPrice.prev_close).toFixed(2)}
                </span>
              </div>
            )}
            <div className="grid grid-cols-3 gap-4 text-sm">
              <div>
                <p className="text-gray-400">Open</p>
                <p className="text-white font-semibold">{sensexPrice?.open?.toFixed(2) || '—'}</p>
              </div>
              <div>
                <p className="text-gray-400">High</p>
                <p className="text-white font-semibold">{sensexPrice?.high?.toFixed(2) || '—'}</p>
              </div>
              <div>
                <p className="text-gray-400">Low</p>
                <p className="text-white font-semibold">{sensexPrice?.low?.toFixed(2) || '—'}</p>
              </div>
            </div>
          </div>
        </div>

        {/* Prediction Insights */}
        {niftyData && (
          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6 mb-8">
            <h2 className="text-lg font-semibold text-white mb-4">Prediction Insights</h2>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
              <div>
                <p className="text-sm text-gray-400">Target Open</p>
                <p className="text-xl font-semibold text-white">
                  {niftyData.data.pred_open?.toLocaleString('en-IN') || '—'}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-400">Target High</p>
                <p className="text-xl font-semibold text-green-400">
                  {niftyData.data.pred_high?.toLocaleString('en-IN') || '—'}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-400">Target Low</p>
                <p className="text-xl font-semibold text-red-400">
                  {niftyData.data.pred_low?.toLocaleString('en-IN') || '—'}
                </p>
              </div>
              <div>
                <p className="text-sm text-gray-400">Confidence</p>
                <p className="text-xl font-semibold text-yellow-400">
                  {(niftyData.data.confidence * 100)?.toFixed(0) || '—'}%
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Order Entry */}
        <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6">
          <h2 className="text-lg font-semibold text-white mb-6">Order Entry</h2>
          
          {orderResult && (
            <div className="mb-6 p-4 bg-green-500/20 border border-green-500/30 rounded-lg text-green-400">
              {orderResult}
            </div>
          )}
          
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <label className="block text-sm text-gray-400 mb-2">Side</label>
              <div className="flex gap-2">
                <button
                  onClick={() => setSide('BUY')}
                  className={`flex-1 py-3 rounded-lg font-semibold transition-colors ${
                    side === 'BUY' ? 'bg-green-600 text-white' : 'bg-gray-800 text-green-400 hover:bg-gray-700'
                  }`}
                >
                  BUY
                </button>
                <button
                  onClick={() => setSide('SELL')}
                  className={`flex-1 py-3 rounded-lg font-semibold transition-colors ${
                    side === 'SELL' ? 'bg-red-600 text-white' : 'bg-gray-800 text-red-400 hover:bg-gray-700'
                  }`}
                >
                  SELL
                </button>
              </div>
            </div>

            <div>
              <label className="block text-sm text-gray-400 mb-2">Order Type</label>
              <select
                value={orderType}
                onChange={(e) => setOrderType(e.target.value as 'MARKET' | 'LIMIT' | 'SL')}
                className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500"
              >
                <option value="MARKET">Market</option>
                <option value="LIMIT">Limit</option>
                <option value="SL">Stop Loss</option>
              </select>
            </div>

            <div>
              <label className="block text-sm text-gray-400 mb-2">Quantity</label>
              <input
                type="number"
                value={quantity}
                onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
                min={1}
                className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500"
              />
            </div>

            <div>
              <label className="block text-sm text-gray-400 mb-2">
                {orderType === 'MARKET' ? 'Market Price' : 'Limit Price'}
              </label>
              <input
                type="number"
                value={limitPrice}
                onChange={(e) => setLimitPrice(e.target.value)}
                placeholder={orderType === 'MARKET' ? niftyPrice?.price?.toFixed(2) || 'Market' : 'Enter limit price'}
                className="w-full px-4 py-3 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          <button
            onClick={handleSubmitOrder}
            className={`w-full mt-6 py-3 rounded-lg font-semibold transition-colors ${
              side === 'BUY' ? 'bg-green-600 hover:bg-green-700 text-white' : 'bg-red-600 hover:bg-red-700 text-white'
            }`}
          >
            {side} {quantity} @ {limitPrice || 'Market'}
          </button>

          <p className="text-xs text-gray-500 mt-4 text-center">
            Last updated: {lastUpdated?.toLocaleTimeString('en-IN')}
          </p>
        </div>

        <ConnectionStatusBadge />
      </div>
    </div>
  );
}
