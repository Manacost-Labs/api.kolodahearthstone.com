import type { Metadata } from 'next';
import localFont from 'next/font/local';
import { getSession } from '@/lib/backend';
import { categories } from '@/lib/navigation';
import { Shell } from '@/components/Shell';
import './globals.css';
import './deck-tiles.css';
const inter=localFont({src:'../public/fonts/inter.ttf',display:'swap',variable:'--font-inter'});
const belwe=localFont({src:'../public/fonts/hearthstone-belwe.ttf',weight:'700',display:'swap',variable:'--font-hearthstone-belwe'});
const benguiat=localFont({src:'../public/fonts/hearthstone-benguiat.ttf',weight:'700',display:'swap',variable:'--font-hearthstone-benguiat'});
export const metadata:Metadata={title:'HS Data · Панель управления',robots:{index:false,follow:false}};
// This tiny local script must run before first paint to avoid flashing the wrong theme.
const themeScript = "try{var theme=localStorage.getItem('hsDataTheme-v2');if(['light','dark','tavern','arcane'].includes(theme))document.documentElement.dataset.theme=theme}catch{}";
export default async function Layout({children}:{children:React.ReactNode}) {
  const session = await getSession();
  return <html lang="ru" data-theme="light" suppressHydrationWarning><head><script dangerouslySetInnerHTML={{__html:themeScript}}/></head><body className={`${inter.variable} ${belwe.variable} ${benguiat.variable}`}><Shell {...session} categories={categories}>{children}</Shell></body></html>;
}
