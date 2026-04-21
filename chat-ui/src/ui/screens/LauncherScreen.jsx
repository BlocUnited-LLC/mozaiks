/**
 * LauncherScreen — default shell renderer for user_choice transitions.
 *
 * This is the default shell renderer for user_choice transitions.
 * It reads route options and presentation props from extension_registry.json.
 *
 * Custom transition components replace this entirely — they receive the same
 * { transition, onResolve } props and can render anything.
 *
 * Props:
 *   transition — full WorkflowTransition object from the registry
 *   onResolve  — (option_id: string) => void
 *                fires routing.transition.resolve; shell executes the routing
 *
 * transition.options fields used:
 *   id
 *
 * transition.ui.props (optional):
 *   title, subtitle, background, button
 *   options: {
 *     [optionId]: { label, description, image, button }
 *   }
 */

import { useCallback } from 'react'
import { LauncherCard } from './LauncherCard.jsx'

const formatOptionLabel = (id) =>
  String(id || 'continue')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toUpperCase()

const asObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return {}
  return value
}

const asString = (value) => {
  return typeof value === 'string' && value.trim() ? value.trim() : null
}

export function LauncherScreen({ transition, onResolve }) {
  const options = transition?.options ?? []
  const screenProps = asObject(transition?.ui?.props)
  const optionPropsById = asObject(screenProps.options)

  const title = asString(screenProps.title) ?? 'Choose Your Path'
  const subtitle = asString(screenProps.subtitle)
  const background = asString(screenProps.background)
  const defaultButton = asString(screenProps.button) ?? 'Continue'

  const handleSelect = useCallback(
    (option) => {
      onResolve?.(option.id)
    },
    [onResolve],
  )

  return (
    <div className="relative flex min-h-full flex-1 flex-col bg-background overflow-hidden">

      {background && (
        <img
          src={background}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 z-0 h-full w-full object-cover opacity-30 pointer-events-none select-none"
          draggable={false}
        />
      )}

      <div className="relative z-10 flex min-h-full flex-1 flex-col items-center justify-center px-6 py-12">

        <div className="text-center mb-10 max-w-xl">
          <h1 className="text-3xl md:text-4xl font-bold text-foreground tracking-tight mb-3">
            {title}
          </h1>
          {subtitle && (
            <p className="text-base text-muted-foreground">{subtitle}</p>
          )}
        </div>

        <div
          className={[
            'flex gap-5 w-full max-w-3xl',
            options.length <= 2
              ? 'flex-col md:flex-row'
              : 'flex-col md:flex-row md:flex-wrap justify-center',
          ].join(' ')}
        >
          {options.map((option, i) => {
            const optionProps = asObject(optionPropsById?.[option.id])
            return (
              <LauncherCard
                key={option.id ?? i}
                plan={asString(optionProps.label) ?? formatOptionLabel(option.id)}
                description={asString(optionProps.description) ?? ''}
                image={asString(optionProps.image)}
                button={asString(optionProps.button) ?? defaultButton}
                onClick={() => handleSelect(option)}
              />
            )
          })}
        </div>

      </div>
    </div>
  )
}

export default LauncherScreen
