import MessagingProfileTab from "./components/MessagingProfileTab.jsx";

export function register(registerComponent) {
  if (typeof registerComponent !== "function") return;
  registerComponent("MessagingProfileTab", MessagingProfileTab);
}
