import type { ClassSlug } from '@/lib/archetypes';
import { classLabel } from '@/lib/format';
import { classIconUrl } from './class-icons';

/** Class art from HearthPulse (assets/class-icons, bundled); no class means the neutral Hearthstone swirl. */
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
      src={classIconUrl(slug, size > 48 ? 96 : 64)}
      width={size}
      height={size}
      alt={slug ? classLabel(slug) : 'Класс не определён'}
      loading="lazy"
      decoding="async"
    />
  );
}
