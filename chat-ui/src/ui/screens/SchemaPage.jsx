/**
 * SchemaPage — route component that fetches and renders a declarative AppPageSchema.
 *
 * This is registered in the component registry as "SchemaPage".
 * Navigation entries that reference schema-driven generated pages use:
 *
 *   { "path": "/dashboard", "component": "SchemaPage", "schema": "Dashboard" }
 *
 * SchemaPage reads route.schema (or route.path.split('/').pop()) to know
 * which page schema to fetch from /api/pages/{name}.
 *
 * The fetched JSON is passed to PageRenderer, which adapts the declarative
 * schema into live primitive props and interactions. The schema still drives
 * everything; route loading stays dumb.
 */

import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { PageRenderer } from '../page-renderer/index.js';

function LoadingPage() {
  return (
    <div className="flex min-h-full items-center justify-center px-4 py-10">
      <div className="flex flex-col items-center gap-3">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        <p className="text-sm text-muted-foreground">Loading page…</p>
      </div>
    </div>
  );
}

function ErrorPage({ message }) {
  return (
    <div className="flex min-h-full items-center justify-center px-4 py-10">
      <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center max-w-md">
        <p className="text-sm font-bold text-destructive mb-2">Failed to load page</p>
        <p className="text-xs text-muted-foreground">{message}</p>
      </div>
    </div>
  );
}

/**
 * Derive the schema name from the route object.
 * Prefers route.schema, falls back to the last path segment.
 */
function resolveSchemaName(route) {
  if (route?.schema && typeof route.schema === 'string') {
    return route.schema;
  }
  const path = typeof route?.path === 'string' ? route.path : '';
  return path.split('/').filter(Boolean).pop() ?? null;
}

/**
 * @param {{ route: object }} props
 */
export function SchemaPage({ route }) {
  const schemaName = resolveSchemaName(route);
  const navigate = useNavigate();

  const [schema, setSchema]   = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError]     = useState(null);

  useEffect(() => {
    if (!schemaName) {
      setError('No schema name provided for this route.');
      setLoading(false);
      return;
    }

    let cancelled = false;
    setLoading(true);
    setError(null);

    fetch(`/api/pages/${encodeURIComponent(schemaName)}`, {
      headers: { 'Content-Type': 'application/json' },
    })
      .then((res) => {
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        return res.json();
      })
      .then((data) => {
        if (!cancelled) {
          setSchema(data);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message);
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, [schemaName]);

  if (loading) return <LoadingPage />;
  if (error)   return <ErrorPage message={error} />;
  if (!schema) return <ErrorPage message="Empty schema returned from server." />;

  return <PageRenderer schema={schema} onNavigate={navigate} />;
}

export default SchemaPage;
