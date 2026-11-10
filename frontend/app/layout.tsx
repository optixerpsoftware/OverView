import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
import Providers from '../components/Providers';
import Shell from '../components/Shell';

export const metadata: Metadata = { title: 'OverView' };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="fr" suppressHydrationWarning>
      <head>
        {/* Applique le thème choisi avant l'affichage (évite un flash clair en thème sombre). */}
        <script
          dangerouslySetInnerHTML={{
            __html:
              "try{var t=JSON.parse(localStorage.getItem('overview-ui')||'{}').state;t=t&&t.theme;if(t==='dark'||t==='light')document.documentElement.setAttribute('data-theme',t)}catch(e){}",
          }}
        />
      </head>
      <body>
        <Providers>
          <Shell>{children}</Shell>
        </Providers>
      </body>
    </html>
  );
}
