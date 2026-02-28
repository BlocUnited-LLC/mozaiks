// ============================================================================
// THEME SYSTEM
// ============================================================================
// Simple: loadBrand('assets') → reads /assets/brand.json
// ============================================================================

import { useState, useEffect, createContext, useContext } from 'react';
import loadBrand, { applyBrand } from './loadBrand';

// ─── Context ──────────────────────────────────────────────────────────────

const BrandContext = createContext(null);

// ─── Provider ─────────────────────────────────────────────────────────────

export function BrandProvider({ children }) {
  const [brand, setBrand] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    setLoading(true);
    loadBrand()
      .then((b) => {
        applyBrand(b);
        setBrand(b);
      })
      .catch((e) => {
        console.error('Failed to load brand:', e);
        setError(e);
      })
      .finally(() => setLoading(false));
  }, [brandId]);

  return (
    <BrandContext.Provider value={{ brand, loading, error }}>
      {children}
    </BrandContext.Provider>
  );
}

// ─── Hook ─────────────────────────────────────────────────────────────────

export function useBrand() {
  const ctx = useContext(BrandContext);
  if (!ctx) throw new Error('useBrand must be inside BrandProvider');
  return ctx;
}

// ─── Exports ──────────────────────────────────────────────────────────────

export { loadBrand, applyBrand };
