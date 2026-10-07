'use client';
import type { CSSProperties } from 'react';
import { deckTile, normalizedCard } from '@/lib/model';
import type { Row } from '@/lib/types';

export function DeckTiles({ cards, cardType, onSelect }: {
  cards: ReturnType<typeof normalizedCard>[];
  cardType: string;
  onSelect: (row: Row) => void;
}) {
  return <div className="deck-grid-viewport"><div className="deck-grid">
    {cards.map(card => {
      const tile = deckTile(card.row, cardType);
      return <button type="button" key={card.id} className="deck-tile" lang={/[А-Яа-яЁё]/.test(card.name) ? 'ru' : 'en'}
        style={{ '--rarity': tile.color, '--tile-background': tile.background } as CSSProperties}
        onClick={() => onSelect(card.row)} aria-label={`${card.name}. Открыть детали`}>
        {tile.image && <img className="deck-tile__art" src={tile.image} alt="" width={320} height={64}
          loading="lazy" decoding="async" onError={event => { event.currentTarget.style.visibility = 'hidden'; }} />}
        <span className={`deck-tile__cost${tile.battlegrounds ? ' deck-tile__cost--tavern' : ''}${tile.cost === '—' ? ' deck-tile__cost--unknown' : ''}`}
          aria-label={tile.battlegrounds ? `Уровень таверны: ${tile.cost}` : tile.cost === '—' ? 'Стоимость не указана' : `Стоимость: ${tile.cost}`}>{tile.cost}</span>
        <span className="deck-tile__name" title={tile.battlegrounds ? `${card.name} · ${card.tribe}` : card.name}>{card.name}</span>
        <span className={`deck-tile__quantity${tile.battlegrounds ? ' deck-tile__quantity--gold' : ''}`}
          title={tile.battlegrounds && tile.price ? `Цена покупки: ${tile.price} золота` : undefined}
          aria-label={tile.battlegrounds && tile.price ? `Цена покупки: ${tile.price} золота` : tile.legendary ? 'Легендарная карта' : undefined}>{tile.price}</span>
      </button>;
    })}
  </div></div>;
}
