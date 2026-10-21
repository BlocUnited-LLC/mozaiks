import MessagingProfileTab from "./components/MessagingProfileTab.jsx";
import Messages from "./pages/custom/Messages.jsx";

export function register(registerComponent) {
  if (typeof registerComponent !== "function") return;
  registerComponent("MessagingProfileTab", MessagingProfileTab);
  registerComponent("Messages", Messages);
}
