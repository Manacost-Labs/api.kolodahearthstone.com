// Class icons as bundled assets: Next serves them under /_next/static/media/, the only static path nginx
// sends to panel-next (panel/nginx/api.kolodahearthstone.com.conf). A file in public/ would 404 behind nginx (#107).
import type { StaticImageData } from 'next/image';
import type { ClassSlug } from '@/lib/archetypes';
import all96 from '@/assets/class-icons/all-96.webp';
import deathknight64 from '@/assets/class-icons/deathknight-64.webp';
import deathknight96 from '@/assets/class-icons/deathknight-96.webp';
import demonhunter64 from '@/assets/class-icons/demonhunter-64.webp';
import demonhunter96 from '@/assets/class-icons/demonhunter-96.webp';
import druid64 from '@/assets/class-icons/druid-64.webp';
import druid96 from '@/assets/class-icons/druid-96.webp';
import hunter64 from '@/assets/class-icons/hunter-64.webp';
import hunter96 from '@/assets/class-icons/hunter-96.webp';
import mage64 from '@/assets/class-icons/mage-64.webp';
import mage96 from '@/assets/class-icons/mage-96.webp';
import paladin64 from '@/assets/class-icons/paladin-64.webp';
import paladin96 from '@/assets/class-icons/paladin-96.webp';
import priest64 from '@/assets/class-icons/priest-64.webp';
import priest96 from '@/assets/class-icons/priest-96.webp';
import rogue64 from '@/assets/class-icons/rogue-64.webp';
import rogue96 from '@/assets/class-icons/rogue-96.webp';
import shaman64 from '@/assets/class-icons/shaman-64.webp';
import shaman96 from '@/assets/class-icons/shaman-96.webp';
import warlock64 from '@/assets/class-icons/warlock-64.webp';
import warlock96 from '@/assets/class-icons/warlock-96.webp';
import warrior64 from '@/assets/class-icons/warrior-64.webp';
import warrior96 from '@/assets/class-icons/warrior-96.webp';

const icons: Record<ClassSlug, Record<64 | 96, StaticImageData>> = {
  deathknight: { 64: deathknight64, 96: deathknight96 },
  demonhunter: { 64: demonhunter64, 96: demonhunter96 },
  druid: { 64: druid64, 96: druid96 },
  hunter: { 64: hunter64, 96: hunter96 },
  mage: { 64: mage64, 96: mage96 },
  paladin: { 64: paladin64, 96: paladin96 },
  priest: { 64: priest64, 96: priest96 },
  rogue: { 64: rogue64, 96: rogue96 },
  shaman: { 64: shaman64, 96: shaman96 },
  warlock: { 64: warlock64, 96: warlock96 },
  warrior: { 64: warrior64, 96: warrior96 },
};

/** The icon URL for a class; no class means the neutral Hearthstone swirl (96 px only). */
export function classIconUrl(slug: ClassSlug | null, size: 64 | 96): string {
  return slug ? icons[slug][size].src : all96.src;
}
