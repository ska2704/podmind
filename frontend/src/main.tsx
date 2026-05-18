import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import App from './App.tsx';
import './index.css';

/**
 * Single QueryClient for the whole app.
 *
 * `placeholderData: (prev) => prev` is set on every poll query
 * individually rather than globally so a one-off query (`/ask`) keeps
 * its own behaviour. Default staleTime is 0 — polling intervals are
 * authoritative.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Don't auto-refetch on focus — the demo is single-viewport,
      // and refetch-on-focus produces a visible flash mid-recording.
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
