import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'ZERO Terminal - Quantitative Intelligence Platform',
  description: 'Hedge Fund Manager Terminal with real-time market intelligence',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <body className="bg-gray-950 text-gray-100 min-h-screen">
        {children}
      </body>
    </html>
  );
}
