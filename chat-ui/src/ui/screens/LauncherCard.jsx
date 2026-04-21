/**
 * LauncherCard — a single selectable option on a LauncherScreen.
 *
 * Equivalent to PlotCourseComponent / the selection cards from MOZ-UI.
 * Receives all content as props — no hardcoded text or images.
 */

import { useState } from 'react'

/**
 * @param {{
 *   plan: string,
 *   description: string,
 *   image: string,
 *   button: string,
 *   onClick: () => void,
 *   disabled?: boolean,
 * }} props
 */
export function LauncherCard({ plan, description, image, button, onClick, disabled = false }) {
  const [hovered, setHovered] = useState(false)

  return (
    <button
      onClick={disabled ? undefined : onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      disabled={disabled}
      className={[
        'flex flex-col items-center text-center rounded-2xl border transition-all duration-200',
        'bg-card/80 backdrop-blur-sm p-6 gap-4 w-full flex-1',
        hovered && !disabled
          ? 'border-primary/60 shadow-lg shadow-primary/10 scale-[1.02]'
          : 'border-border/50',
        disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
      ].join(' ')}
    >
      {/* Image */}
      {image && (
        <div className="w-full flex justify-center">
          <img
            src={image}
            alt={plan}
            className="h-40 w-auto object-contain rounded-xl"
            draggable={false}
          />
        </div>
      )}

      {/* Plan label */}
      <p className="text-xs font-bold tracking-widest uppercase text-primary">
        {plan}
      </p>

      {/* Description */}
      <p className="text-sm text-muted-foreground leading-relaxed flex-1">
        {description}
      </p>

      {/* CTA button */}
      <span
        className={[
          'mt-2 px-6 py-2 rounded-full text-xs font-bold tracking-widest uppercase transition-colors',
          disabled
            ? 'bg-muted text-muted-foreground'
            : 'bg-primary text-primary-foreground hover:bg-primary/90',
        ].join(' ')}
      >
        {button}
      </span>
    </button>
  )
}

export default LauncherCard
