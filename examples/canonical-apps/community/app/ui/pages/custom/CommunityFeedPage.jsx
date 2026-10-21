import React from 'react';
import { useChatUI } from '@mozaiks/chat-ui/core';
import { CommunityRoom } from '../../components/CommunityRoom.jsx';

export default function CommunityFeedPage() {
  const { user } = useChatUI();
  return <CommunityRoom key={user?.id || 'signed-out'} user={user} />;
}
