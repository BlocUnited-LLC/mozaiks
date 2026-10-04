import CommunityFeedPage from './pages/custom/CommunityFeedPage.jsx';
import CommunityPostPage from './pages/custom/CommunityPostPage.jsx';

export function register(registerComponent) {
  registerComponent('CommunityFeedPage', CommunityFeedPage);
  registerComponent('CommunityPostPage', CommunityPostPage);
}
