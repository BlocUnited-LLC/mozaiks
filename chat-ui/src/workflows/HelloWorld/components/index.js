// ==============================================================================
// FILE: workflows/HelloWorld/components/index.js
// DESCRIPTION: Export HelloWorld workflow-specific UI components.
//
// Components registered here are resolved by the runtime when a tool fires
// with a matching `ui.component` value in tools.yaml.
//
// To add a component:
//   1. Create a new file in this folder (e.g. MyComponent.js)
//   2. Import it here and add it to HelloWorldComponents
// ==============================================================================

import GreetingCard from './GreetingCard';

/**
 * HelloWorld workflow components.
 *
 * Key    → matches `ui.component` in tools.yaml
 * Value  → React component rendered by the ChatUI runtime
 */
const HelloWorldComponents = {
  GreetingCard,
};

export default HelloWorldComponents;
