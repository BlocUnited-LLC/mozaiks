import React from 'react';
import { ArtifactPreviewPane } from '@mozaiks/chat-ui';
import { readJson } from './OwnerAccessCard.jsx';

export default function PreviewPane({ files, ...props }) {
  return <ArtifactPreviewPane {...props} authRequired={readJson(files, 'app.json').authRequired === true} />;
}
