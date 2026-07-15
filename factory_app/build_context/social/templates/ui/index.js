import { ActivityFeedTab, FriendListTab, UserPostsTab } from "./components/SocialProfileTabs.jsx";

export function register(registerComponent) {
  if (typeof registerComponent !== "function") return;
  registerComponent("FriendListTab", FriendListTab);
  registerComponent("ActivityFeedTab", ActivityFeedTab);
  registerComponent("UserPostsTab", UserPostsTab);
}
