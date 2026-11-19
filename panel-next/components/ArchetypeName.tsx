import { archetypeIdentity } from '@/lib/archetypes';
import { ClassIcon } from './ClassIcon';
import './archetype-name.css';

/** An archetype cell: class icon, the Russian name from the dictionary and the English one below it (#107). */
/** `classValue` is the row's class (`class_name` from #104, `class` of decks); the name's ending is the fallback. */
export function ArchetypeName({ name, classValue }: { name: string; classValue?: unknown }) {
  const identity = archetypeIdentity(name, classValue);
  return (
    <span className="archetype-name">
      <ClassIcon slug={identity.classSlug} size={28} className="archetype-name__icon" />
      <span className="archetype-name__text">
        <span>{identity.title}</span>
        {identity.subtitle ? <small lang="en">{identity.subtitle}</small> : null}
      </span>
    </span>
  );
}
