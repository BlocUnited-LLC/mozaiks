/**
 * LauncherCard — a single selectable option on a LauncherScreen.
 *
 * Equivalent to PlotCourseComponent / the selection cards from MOZ-UI.
 * Receives all content as props — no hardcoded text or images.
 */

/**
 * @param {{
 *   plan: string,
 *   description: string,
 *   image: string,
 *   button: string,
 *   onClick: () => void,
 *   disabled?: boolean,
 *   helperText?: string,
 *   badge?: string,
 *   style?: object,
 * }} props
 */
export function LauncherCard({
  plan,
  description,
  image,
  button,
  onClick,
  disabled = false,
  helperText = '',
  badge = '',
  style,
}) {
  const interactive = !disabled && typeof onClick === 'function';

  return (
    <button
      type="button"
      onClick={interactive ? onClick : undefined}
      aria-disabled={disabled}
      title={disabled && helperText ? helperText : undefined}
      className={[
        'group relative flex flex-col overflow-hidden rounded-[1.5rem] border bg-card/78 p-5 text-left focus:outline-none focus:ring-2 focus:ring-primary/60 focus:ring-offset-2 focus:ring-offset-background',
        interactive
          ? 'cursor-pointer border-border/70 transition hover:-translate-y-1 hover:border-primary/60 hover:bg-card hover:shadow-2xl'
          : 'cursor-not-allowed border-border/60 bg-card/60 opacity-80',
      ].join(' ')}
      style={{
        width: '100%',
        maxWidth: '24rem',
        flex: '1 1 20rem',
        boxShadow: interactive
          ? '0 24px 60px -40px rgba(15, 23, 42, 0.85)'
          : '0 18px 44px -40px rgba(15, 23, 42, 0.65)',
        ...style,
      }}
    >
      {badge ? (
        <span className="absolute right-4 top-4 z-10 rounded-full border border-border/70 bg-background/85 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {badge}
        </span>
      ) : null}

      {image && (
        <div className="mb-5 flex h-44 w-full items-center justify-center overflow-hidden rounded-2xl border border-border/60 bg-muted/20 p-4">
          <img
            src={image}
            alt=""
            aria-hidden="true"
            className="max-h-full max-w-full object-contain"
            draggable={false}
          />
        </div>
      )}

      <h2 className="text-lg font-semibold text-foreground">{plan}</h2>
      {description ? (
        <p className="mt-3 flex-1 text-sm leading-6 text-muted-foreground">{description}</p>
      ) : null}
      {helperText ? (
        <p className={[
          'mt-4 text-xs leading-5',
          disabled ? 'text-warning' : 'text-muted-foreground',
        ].join(' ')}>
          {helperText}
        </p>
      ) : null}
      <span
        className={[
          'mt-5 inline-flex min-h-11 items-center justify-center rounded-xl border px-4 py-3 text-sm font-semibold transition-colors',
          disabled
            ? 'border-border/70 bg-muted text-muted-foreground'
            : 'border-primary/40 bg-primary text-primary-foreground group-hover:bg-primary/90',
        ].join(' ')}
      >
        {button}
      </span>
    </button>
  );
}

export default LauncherCard
