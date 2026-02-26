/**
 * hello_world workflow — frontend module
 *
 * This wires the "hello_world" backend workflow to its UI artifact component.
 * Copy this file (and its folder) to create a new workflow:
 *
 *   cp -r workflows/hello_world workflows/my_workflow
 *   # Rename the component and update the `name` below
 *
 * Backend counterpart lives at:
 *   workflows/hello_world/backend/orchestrator.yaml
 *
 * The `artifactComponent` is rendered by the chat UI when the backend
 * streams a workflow_artifact event with matching workflow name.
 */

import HelloWorldArtifact from './HelloWorldArtifact';

const helloWorldWorkflow = {
  // Must match the workflow name used in your backend orchestrator.yaml
  name: 'hello_world',

  // Human-readable label shown in the chat UI workflow selector
  label: 'Hello World',

  // Short description shown as a suggestion/hint in the chat input
  description: 'A starter workflow — say hello and see how output renders.',

  // The React component that renders the workflow's artifact output.
  // Receives: { data, status, onAction }
  artifactComponent: HelloWorldArtifact,

  // Optional: prompt suggestions shown to the user before they type
  suggestions: [
    'Say hello',
    'Greet me',
    'Hello world!',
  ],
};

export default helloWorldWorkflow;
