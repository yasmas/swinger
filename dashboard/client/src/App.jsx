import { useState, useEffect, useCallback } from 'react';
import { useWebSocket } from './hooks/useWebSocket.js';
import TradingDashboard from './TradingDashboard.jsx';

export default function App() {
  const [bots, setBots] = useState([]);
  const [loading, setLoading] = useState(true);
  const [tradeTick, setTradeTick] = useState(0);

  // Fetch initial bot list
  useEffect(() => {
    fetch('/api/bots')
      .then(r => r.json())
      .then(data => {
        setBots(data);
        setLoading(false);
      })
      .catch(err => {
        console.error('Failed to fetch bots:', err);
        setLoading(false);
      });
  }, []);

  // WebSocket handler
  const handleWsMessage = useCallback((msg) => {
    switch (msg.event) {
      case 'init':
        setBots(msg.data);
        break;

      case 'bot_update':
      case 'bot_connected':
        setBots(prev => prev.map(b =>
          b.name === msg.bot ? { ...b, ...msg.data } : b
        ));
        break;

      case 'bot_disconnected':
        setBots(prev => prev.map(b =>
          b.name === msg.bot ? { ...b, status: 'stopped' } : b
        ));
        break;

      case 'trade_entry':
      case 'trade_exit':
        setTradeTick(t => t + 1);
        break;
    }
  }, []);

  useWebSocket('/ws', handleWsMessage);

  if (loading) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh', color: '#94a3b8', fontSize: 16 }}>
        Loading dashboard...
      </div>
    );
  }

  return <TradingDashboard bots={bots} setBots={setBots} tradeTick={tradeTick} />;
}
