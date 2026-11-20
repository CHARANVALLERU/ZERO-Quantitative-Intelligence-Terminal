'use client';

import { useNews, NewsItem } from '@/hooks/useApi';
import { ConnectionStatusBadge } from '@/hooks/useConnection';
import { useState } from 'react';

function NewsCard({ item }: { item: NewsItem }) {
  const [isRead, setIsRead] = useState(false);

  const handleClick = () => {
    setIsRead(true);
    window.open(item.link, '_blank');
  };

  const impactColors = {
    extreme_bullish: 'bg-green-500',
    bullish: 'bg-green-400',
    neutral: 'bg-gray-500',
    bearish: 'bg-red-400',
    strong_bearish: 'bg-red-500',
  };

  const impactColor = impactColors[item.direction?.toLowerCase() as keyof typeof impactColors] || 'bg-gray-500';

  return (
    <article
      className={`rounded-lg p-4 cursor-pointer transition-all duration-300 ${
        isRead ? 'bg-gray-800/50 opacity-60' : 'bg-gray-800 hover:bg-gray-700/50'
      } border-l-4 ${impactColor}`}
      onClick={handleClick}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <h3 className="font-semibold text-gray-200 mb-2 line-clamp-2">{item.title}</h3>
          <div className="flex items-center gap-2 text-xs text-gray-500">
            <span className="px-2 py-0.5 rounded bg-gray-700">{item.source}</span>
            <span>{item.category_label}</span>
            {item.published && (
              <span>{new Date(item.published).toLocaleString('en-IN')}</span>
            )}
          </div>
        </div>
        <div className={`flex items-center gap-1 px-2 py-1 rounded-full text-xs font-semibold ${
          item.impact_score > 70 ? 'bg-red-500/20 text-red-400' :
          item.impact_score > 40 ? 'bg-yellow-500/20 text-yellow-400' :
          'bg-green-500/20 text-green-400'
        }`}>
          <span className="font-bold">{item.impact_score?.toFixed(0)}</span>
          <span>/100</span>
        </div>
      </div>
    </article>
  );
}

export default function NewsPage() {
  const { news, breaking, loading, error } = useNews(25);
  const [filter, setFilter] = useState('all');
  const [search, setSearch] = useState('');

  // Filter news
  const filteredNews = news.filter(item => {
    const matchesFilter = filter === 'all' || 
      (filter === 'breaking' && item.impact_score > 70) ||
      (filter === 'bullish' && item.direction === 'BULLISH') ||
      (filter === 'bearish' && item.direction === 'BEARISH');
    const matchesSearch = !search || 
      item.title.toLowerCase().includes(search.toLowerCase()) ||
      item.source.toLowerCase().includes(search.toLowerCase());
    return matchesFilter && matchesSearch;
  });

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100">
        <div className="container mx-auto px-4 py-8">
          <h1 className="text-3xl font-bold text-white mb-8">Market News</h1>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {[...Array(6)].map((_, i) => (
              <div key={i} className="h-32 bg-gray-800 rounded-lg animate-pulse"></div>
            ))}
          </div>
        </div>
        <ConnectionStatusBadge />
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
        <div className="text-center">
          <p className="text-red-400 text-lg">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <div className="container mx-auto px-4 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white mb-4">Market News</h1>
          <p className="text-gray-400">Real-time market intelligence and breaking alerts</p>
        </div>

        {/* Controls */}
        <div className="flex flex-wrap items-center gap-4 mb-6">
          <div className="flex gap-2">
            {['all', 'breaking', 'bullish', 'bearish'].map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
                  filter === f ? 'bg-blue-600 text-white' : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
                }`}
              >
                {f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
          <div className="flex-1">
            <input
              type="text"
              placeholder="Search news..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="w-full px-4 py-2 bg-gray-800 border border-gray-700 rounded-lg text-gray-200 placeholder-gray-500 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>

        {/* Breaking News */}
        {breaking.length > 0 && (
          <div className="mb-8">
            <h2 className="text-lg font-semibold text-red-400 mb-4 flex items-center gap-2">
              <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse"></span>
              Breaking Alerts ({breaking.length})
            </h2>
            <div className="space-y-4">
              {breaking.slice(0, 3).map(item => (
                <NewsCard key={item.id} item={item} />
              ))}
            </div>
          </div>
        )}

        {/* News Feed */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filteredNews.map(item => (
            <NewsCard key={item.id} item={item} />
          ))}
        </div>

        {filteredNews.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            <p className="text-lg">No news found matching your filters</p>
          </div>
        )}
      </div>
      <ConnectionStatusBadge />
    </div>
  );
}
