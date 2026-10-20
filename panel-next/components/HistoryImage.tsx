import { archivedHistoryImage, historyDate, type HistoryVersion } from '@/lib/card-history';

export function HistoryImage({ version, label }: { version: HistoryVersion; label: string }) {
  const image = archivedHistoryImage(version);
  return (
    <figure className="history-image">
      {image ? (
        // Content-addressed snapshots retain their bytes and aspect ratio.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={image.url}
          alt={`${label}: сохранённое изображение карты`}
          width={256}
          height={384}
          loading="lazy"
        />
      ) : (
        <p className="muted">Изображение этой версии ещё не подтверждено.</p>
      )}
      <figcaption>
        <strong>{label}</strong>
        {image && <span>Сохранено {historyDate(image.captured_at)}</span>}
      </figcaption>
    </figure>
  );
}
