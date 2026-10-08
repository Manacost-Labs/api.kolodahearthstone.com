import type { Metadata } from 'next';
import localFont from 'next/font/local';
import { getSession } from '@/lib/backend';
import { categories } from '@/lib/navigation';
import { Shell } from '@/components/Shell';
import { themeBootScript } from '@/lib/theme';
import './globals.css';
import './deck-tiles.css';
// Inter 4 variable font subset to Latin, Cyrillic and punctuation; rebuild with scripts/subset-inter.sh.
const inter = localFont({
  src: '../public/fonts/inter.woff2',
  weight: '100 900',
  display: 'swap',
  variable: '--font-inter',
});
const belwe = localFont({
  src: '../public/fonts/hearthstone-belwe.ttf',
  weight: '700',
  display: 'swap',
  variable: '--font-hearthstone-belwe',
});
const benguiat = localFont({
  src: '../public/fonts/hearthstone-benguiat.ttf',
  weight: '700',
  display: 'swap',
  variable: '--font-hearthstone-benguiat',
});
export const metadata: Metadata = {
  title: 'HS Data · Панель управления',
  robots: { index: false, follow: false },
};
export default async function Layout({ children }: { children: React.ReactNode }) {
  const session = await getSession();
  return (
    <html lang="ru" data-theme="light" data-theme-preference="light" suppressHydrationWarning>
      <head>
        {/* Runs before first paint so the stored or system theme never flashes. */}
        {/* biome-ignore lint/security/noDangerouslySetInnerHtml: static script built from lib/theme.ts constants */}
        <script dangerouslySetInnerHTML={{ __html: themeBootScript }} />
      </head>
      <body className={`${inter.variable} ${belwe.variable} ${benguiat.variable}`}>
        <Shell {...session} categories={categories}>
          {children}
        </Shell>
      </body>
    </html>
  );
}
