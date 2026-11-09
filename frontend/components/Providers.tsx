'use client';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { queryClient } from '../lib/queryClient';
import RealtimeBridge from './RealtimeBridge';
import Toasts from './Toasts';

export default function Providers({ children }: { children: ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <RealtimeBridge />
      {children}
      <Toasts />
    </QueryClientProvider>
  );
}
