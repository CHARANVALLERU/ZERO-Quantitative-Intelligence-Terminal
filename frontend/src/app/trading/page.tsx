import { useLivePrices } from '@/hooks/useApi';
import { ConnectionStatusBadge } from '@/hooks/useConnection';
import { useState } from 'react';

function TradingCard({ symbol, name }: { symbol: string; name: string }) {
  const { prices, loading, lastUpdated } = useLivePrices();
  const priceData = prices[symbol];
  const [side, setSide] = useState<'BUY' | 'SELL'>('BUY');
  const [quantity, setQuantity] = useState(1);
  const [limitPrice, setLimitPrice] = useState('');
  const [orderResult, setOrderResult] = useState<string | null>(null);

  const handleSubmitOrder = () => {
    setOrderResult(`Order ${side} ${quantity} shares at ${limitPrice || 'market price'} - ${limitPrice ? 'LIMIT' : 'MARKET'} order placed successfully`);
    setTimeout(() => setOrderResult(null), 5000);
  };

  if (loading || !priceData) {
    return (
      <div className="rounded-xl bg-gray-900/50 border border-gray-700 p-4 animate-pulse">
        <div className="h-6 bg-gray-700 rounded mb-3"></div>
        <div className="h-8 bg-gray-800 rounded"></div>
      </div>
    );
  }

  const price = priceData.price;
  const change = price - priceData.prev_close;
  const up = change >= 0;

  return (
    <div className="rounded-xl bg-gray-900/50 border border-gray-700 p-4 glass">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300">{name}</h3>
        <span className={`text-xs font-bold ${up ? 'text-green-400' : 'text-red-400'}`}>
          {up ? '▲' : '▼'} {Math.abs(change).toFixed(2)}
        </span>
      </div>

      <div className="text-2xl font-bold text-white mb-3">
        {price?.toLocaleString('en-IN')}
      </div>

      <div className="grid grid-cols-3 gap-2 text-xs mb-4">
        <div className="text-center">
          <p className="text-gray-500">Open</p>
          <p className="text-gray-300">{priceData.open?.toFixed(2) || '—'}</p>
        </div>
        <div className="text-center">
          <p className="text-gray-500">High</p>
          <p className="text-green-400">{priceData.high?.toFixed(2) || '—'}</p>
        </div>
        <div className="text-center">
          <p className="text-gray-500">Low</p>
          <p className="text-red-400">{priceData.low?.toFixed(2) || '—'}</p>
        </div>
      </div>

      <div className="space-y-2 mb-4">
        <button
          onClick={() => setSide('BUY')}
          className={`w-full py-2 rounded-lg text-sm font-semibold transition-colors ${
            side === 'BUY' ? 'bg-green-600 text-white' : 'bg-gray-800 text-green-400 hover:bg-gray-700'
          }`}
        >
          BUY
        </button>
        <button
          onClick={() => setSide('SELL')}
          className={`w-full py-2 rounded-lg text-sm font-semibold transition-colors ${
            side === 'SELL' ? 'bg-red-600 text-white' : 'bg-gray-800 text-red-400 hover:bg-gray-700'
          }`}
        >
          SELL
        </button>
      </div>

      <div className="space-y-2 mb-3">
        <input
          type="number"
          value={quantity}
          onChange={(e) => setQuantity(parseInt(e.target.value) || 1)}
          min={1}
          placeholder="Quantity"
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 text-sm"
        />
        <input
          type="number"
          value={limitPrice}
          onChange={(e) => setLimitPrice(e.target.value)}
          placeholder="Limit Price (optional)"
          className="w-full px-3 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 text-sm"
        />
      </div>

      <button
        onClick={handleSubmitOrder}
        className={`w-full py-2 rounded-lg text-sm font-semibold transition-colors ${
          side === 'BUY' ? 'bg-green-600 hover:bg-green-700 text-white' : 'bg-red-600 hover:bg-red-700 text-white'
        }`}
      >
        {side} {quantity}
      </button>

      {orderResult && (
        <div className="mt-2 p-2 bg-green-500/20 border border-green-500/30 rounded-lg text-green-400 text-xs">
          {orderResult}
        </div>
      )}
    </div>
  );
}

export default function TradingPage() {
  return (
    <div className="min-h-screen bg-gray-950">
      <div className="container mx-auto px-4 py-8">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-4">Quantum Trading Terminal</h1>
          <p className="text-gray-400">Execute trades with intelligent risk management</p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
          <TradingCard symbol="NIFTY 50" name="NIFTY 50" />
          <TradingCard symbol="BANKNIFTY" name="Bank Nifty" />
          <TradingCard symbol="SENSEX" name="Sensex" />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6 glass">
            <h2 className="text-lg font-semibold text-white mb-4">Order Book</h2>
            <div className="space-y-3">
              {["ORD-202409171012345678", "ORD-202409171012345679", "ORD-202409171012345680"].map((id, i) => (
                <div key={i} className="flex items-center justify-between p-3 bg-gray-800/50 rounded-lg">
                  <span className="text-sm text-gray-300">{id.slice(-6)}</span>
                  <span className="text-sm text-gray-400">NIFTY 50</span>
                  <span className="text-sm text-gray-400">BUY</span>
                  <span className="text-sm text-gray-400">100</span>
                  <span className="text-xs px-2 py-0.5 rounded-full bg-green-500/20 text-green-400">PENDING</span>
                </div>
              ))}
            </div>
          </div>

          <div className="bg-gray-900/50 rounded-xl border border-gray-700 p-6 glass">
            <h2 className="text-lg font-semibold text-white mb-4">Strategy Signals</h2>
            <div className="grid grid-cols-2 gap-4">
              <div className="text-center">
                <p className="text-sm text-gray-400">Active Strategies</p>
                <p className="text-2xl font-bold text-green-400">12</p>
              </div>
              <div className="text-center">
                <p className="text-sm text-gray-400">Win Rate</p>
                <p className="text-2xl font-bold text-yellow-400">72.5%</p>
              </div>
              <div className="text-center">
                <p className="text-sm text-gray-400">Total Returns</p>
                <p className="text-2xl font-bold text-green-400">+18.3%</p>
              </div>
              <div className="text-center">
                <p className="text-sm text-gray-400">Risk Level</p>
                <p className="text-2xl font-bold text-red-400">MEDIUM</p>
              </div>
            </div>
          </div>
        </div>
      </div>
      <ConnectionStatusBadge />
    </div>
  );
}
