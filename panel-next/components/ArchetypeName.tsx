import { archetypeIdentity } from '@/lib/archetypes';
import { ClassIcon } from './ClassIcon';
import './archetype-name.css';

/** An archetype cell: class icon, the Russian name from the dictionary and the English one below it (#107). */
export function ArchetypeName({ name }: { name: string }) {
  const identity = archetypeIdentity(name);
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
