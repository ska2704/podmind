/**
 * Loading skeleton primitives.
 *
 * Two flavours:
 *   - `<Skeleton.Bar />` — a single shimmering rectangle, used for
 *     headers / single lines.
 *   - `<Skeleton.List rows={N} />` — N evenly-spaced bars, used when
 *     a region's structure is "vertical list".
 *
 * The shimmer is a CSS keyframe defined in index.css (`pm-shimmer`).
 * Skeletons should only show on FIRST paint; once data lands, polls
 * keep the previous data via TanStack Query's `placeholderData`.
 */
export function Bar({ w = '100%', h = 12, className = '' }: { w?: number | string; h?: number; className?: string }) {
  return (
    <div
      className={`rounded ${className}`}
      style={{
        width: typeof w === 'number' ? `${w}px` : w,
        height: h,
        background:
          'linear-gradient(90deg, #EFEFEF 0%, #F7F7F7 50%, #EFEFEF 100%)',
        backgroundSize: '200% 100%',
        animation: 'pm-shimmer 1.4s linear infinite',
      }}
    />
  );
}

export function List({ rows, rowHeight = 40, gap = 8 }: { rows: number; rowHeight?: number; gap?: number }) {
  return (
    <div className="flex flex-col" style={{ gap }}>
      {Array.from({ length: rows }).map((_, i) => (
        <Bar key={i} h={rowHeight} />
      ))}
    </div>
  );
}

export const Skeleton = { Bar, List };
