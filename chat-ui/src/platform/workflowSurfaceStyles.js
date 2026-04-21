/**
 * Stable style bridge for legacy workflow UI components outside chat-ui.
 *
 * New workflow UIs should prefer semantic --mz-* Tailwind tokens directly.
 * This bridge exists to keep existing workflow components off orchestration-
 * local adapter files while preserving their current styling contract.
 */

export {
  colors,
  components,
  gradients,
  shadows,
  spacing,
  typography,
} from '../styles/artifactDesignSystem.js';