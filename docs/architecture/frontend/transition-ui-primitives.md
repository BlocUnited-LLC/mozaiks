# Transition UI Primitives

Transition UI is a shell-owned routing surface. Workflow transition files may
wrap shared transition primitives, but they do not own overlay behavior.

## Ownership

- The shell owns modal overlay behavior: backdrop, blur, focus trap, Escape handling, and scroll lock.
- Shared reusable transition primitives live in `@mozaiks/chat-ui/platform`.
- Workflow-local transition files under `extended_orchestration/ui/transitions/` only own product-specific copy, imagery, and option mapping.

## Canonical Primitive Catalog

| Primitive | Import | Use when |
| --- | --- | --- |
| `LauncherScreen` | shell built-in | `ui.props` is enough for a lightweight choice screen |
| `ConfirmScreen` | shell built-in | the transition is a simple confirm/cancel gate |
| `TransitionChoicePanel` | `@mozaiks/chat-ui/platform` | you need a branded modal body with title, subtitle, and grouped choices |
| `TransitionChoiceCard` | `@mozaiks/chat-ui/platform` | you need reusable full-card options with image, badge, helper text, CTA, and disabled state |
| `useTransitionChoiceMotion` | `@mozaiks/chat-ui/platform` | you want shared transition entry motion with reduced-motion support |

## Rules

- Do not import `TransitionOverlayFrame` inside workflow transition files.
- Do not create reusable transition primitive helpers under workflow folders.
- Prefer `LauncherScreen` or `ConfirmScreen` when custom branding is not needed.
- When custom branding is needed, compose `TransitionChoicePanel` and `TransitionChoiceCard` inside a workflow-local wrapper.

## Canonical Wrapper Example

```jsx
import heroImage from '../../../../app/brand/assets/greenfield.png'
import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionChoiceMotion,
} from '@mozaiks/chat-ui/platform'

const OPTION_VIEW = {
  greenfield_app: {
    label: 'Build Something New',
    description: 'Start with a fresh concept and let Mozaiks shape the first build plan.',
    image: heroImage,
    cta: 'Start Build',
  },
  brownfield_app: {
    label: 'Existing App',
    description: 'Bring an existing product into Mozaiks for augmentation and workflow rollout.',
    cta: 'Coming Soon',
    badge: 'Coming Soon',
    disabled: true,
    helperText: 'Existing-app onboarding is staged separately and is not selectable yet.',
  },
}

export default function AppTypeSelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : []
  const motion = useTransitionChoiceMotion()

  return (
    <TransitionChoicePanel
      eyebrow="Start Here"
      title="Choose Your App Journey"
      subtitle="Pick the path that best matches the product you want to build."
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      {options.map((option, index) => {
        const meta = OPTION_VIEW[option.id]
        return (
          <TransitionChoiceCard
            key={option.id}
            optionId={option.id}
            label={meta.label}
            description={meta.description}
            image={meta.image}
            cta={meta.cta}
            badge={meta.badge || ''}
            helperText={meta.helperText || ''}
            disabled={meta.disabled === true}
            onResolve={onResolve}
            entered={motion.entered}
            prefersReducedMotion={motion.prefersReducedMotion}
            delayMs={120 + index * 80}
          />
        )
      })}
    </TransitionChoicePanel>
  )
}
```

## Contract Reminder

The routing contract still lives in `extended_orchestration/extension_registry.json`:

- `ui.component` points to the registered wrapper component name.
- `options[].id` stays semantic.
- `options[].route_to` and optional `context_variables` stay in JSON, not in the wrapper layout.

That keeps the transition routing contract declarative while the visual wrapper stays clean and reusable.