// ==============================================================================
// FILE: chat-ui/src/core/ui/index.js  
// DESCRIPTION: Export shell core UI components
// ==============================================================================

import UserInputRequest from './UserInputRequest';
import ShellUIToolRenderer from './ShellUIToolRenderer';

/**
 * Shell Core UI Components
 * 
 * - UserInputRequest: Generic user input component for agent prompts
 * - ShellUIToolRenderer: Shell-specific tool renderer (routes through eventDispatcher)
 */

const CoreComponents = {
  UserInputRequest,
  ShellUIToolRenderer
};

export default CoreComponents;

export {
  UserInputRequest,
  ShellUIToolRenderer
};
