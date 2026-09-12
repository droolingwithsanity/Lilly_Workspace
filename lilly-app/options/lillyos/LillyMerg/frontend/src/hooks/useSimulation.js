import { useState, useEffect, useCallback } from 'react';

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export function useSimulation() {
  const [state, setState] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const fetchState = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/simulation`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setState(data);
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchState();
    const interval = setInterval(fetchState, 1500);
    return () => clearInterval(interval);
  }, [fetchState]);

  const setSpeed = async (speed) => {
    try {
      await fetch(`${API}/api/simulation/speed?speed=${speed}`);
    } catch (e) {
      console.error(e);
    }
  };

  const toggleSim = async () => {
    try {
      const res = await fetch(`${API}/api/simulation/toggle`);
      const data = await res.json();
      return data.running;
    } catch (e) {
      console.error(e);
    }
  };

  return { state, loading, error, setSpeed, toggleSim };
}
