import React from 'react';
import { useParams } from 'react-router-dom';
import { useChatUI } from '@mozaiks/chat-ui/core';
import { CommunityRoom } from '../../components/CommunityRoom.jsx';

export default function CommunityPostPage() {
  const { user } = useChatUI();
  const { postId } = useParams();
  return <CommunityRoom key={`${user?.id || 'signed-out'}:${postId}`} user={user} postId={postId} />;
}
