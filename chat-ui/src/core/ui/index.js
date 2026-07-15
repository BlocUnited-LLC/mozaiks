// ==============================================================================
// FILE: chat-ui/src/core/ui/index.js
// DESCRIPTION: Export shell core UI components
// ==============================================================================

import UserInputRequest from './UserInputRequest';
import ShellUIToolRenderer from './ShellUIToolRenderer';
import ApprovalCard from './ApprovalCard';
import ChoicePicker from './ChoicePicker';
import ConfirmationSummary from './ConfirmationSummary';
import FormCard from './FormCard';
import DiagramViewer from './DiagramViewer';
import DownloadCenter from './DownloadCenter';
import ArtifactWorkbench from './ArtifactWorkbench';
import EscalationCard from './EscalationCard';

// L1 agent-UI primitives (ui.render event system)
import {
  DataTable,
  Timeline,
  CodeBlock,
  ProgressTracker,
  AlertBanner,
  ActionButton,
  FileList,
  Form,
} from './primitives/index.js';

/**
 * Shell Core UI Components
 *
 * - UserInputRequest: Explicit fallback component for inline workflow text input
 * - ShellUIToolRenderer: Shell-specific tool renderer (routes through eventDispatcher)
 * - Shipped workflow primitives: reusable agent/workflow UI surfaces shared across workflows
 * - L1 primitives: agent-UI building blocks for the ui.render event system
 */

const CoreComponents = {
  UserInputRequest,
  ShellUIToolRenderer,
  ApprovalCard,
  ChoicePicker,
  ConfirmationSummary,
  FormCard,
  EscalationCard,
  DiagramViewer,
  DownloadCenter,
  ArtifactWorkbench,
  // L1 primitives — discoverable by WorkflowUIRouter via component name
  DataTable,
  Timeline,
  CodeBlock,
  ProgressTracker,
  AlertBanner,
  ActionButton,
  FileList,
  Form,
};

export default CoreComponents;

export {
  UserInputRequest,
  ShellUIToolRenderer,
  ApprovalCard,
  ChoicePicker,
  ConfirmationSummary,
  FormCard,
  EscalationCard,
  DiagramViewer,
  DownloadCenter,
  ArtifactWorkbench,
  // L1 primitives
  DataTable,
  Timeline,
  CodeBlock,
  ProgressTracker,
  AlertBanner,
  ActionButton,
  FileList,
  Form,
};
