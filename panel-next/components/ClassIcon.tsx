import { type ClassSlug, classIconSrc } from '@/lib/archetypes';
import { classLabel } from '@/lib/format';

const base = process.env.NEXT_PUBLIC_PANEL_BASE_PATH || '';

/** Class art from HearthPulse (public/class-icons); no class means the neutral Hearthstone swirl. */
export function ClassIcon({
  slug,
  size = 32,
  className,
}: {
  slug: ClassSlug | null;
  size?: number;
  className?: string;
}) {
  return (
    <img
      className={className}
      src={classIconSrc(slug, size > 48 ? 96 : slug ? 64 : 96, base)}
      width={size}
      height={size}
      alt={slug ? classLabel(slug) : 'Класс не определён'}
      loading="lazy"
      decoding="async"
    />
  );
}
