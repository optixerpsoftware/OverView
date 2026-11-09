import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
import Providers from '../components/Providers';
import Shell from '../components/Shell';

export const metadata: Metadata = { title: 'OverView' };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="fr">
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
