// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/ExportActions.js
// DESCRIPTION: Export/download actions using the shipped DownloadCenter workflow primitive.
// ==============================================================================

import React from 'react';
import DownloadCenter from '@mozaiks/chat-ui/core/ui/DownloadCenter.js';

const ExportActions = (props) => {
  return <DownloadCenter {...props} />;
};

export default ExportActions;

