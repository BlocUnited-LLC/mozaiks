// ==============================================================================
// FILE: ChatUI/src/workflows/AppGenerator/components/ExportActions.js
// DESCRIPTION: Export/download actions (wraps the existing FileDownloadCenter UI)
// ==============================================================================

import React from 'react';
import FileDownloadCenter from '../../AgentGenerator/ui/FileDownloadCenter.js';

const ExportActions = (props) => {
  return <FileDownloadCenter {...props} />;
};

export default ExportActions;

