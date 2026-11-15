export default function OwnerAccessCard({ files }) {
  let emails = [];
  try {
    const manifest = JSON.parse(
      files["app.json"] || files["app/app.json"] || "{}",
    );
    if (Array.isArray(manifest.admins))
      emails = manifest.admins.filter((email) => typeof email === "string");
  } catch {
    // An incomplete artifact has no owner handoff yet.
  }
  return (
    <section
      aria-label="App owner access"
      className="rounded-xl border border-white/10 p-4 text-sm text-[var(--color-text-primary)]"
    >
      <h3 className="font-semibold">Owner access</h3>
      {emails.length > 0 ? (
        <p className="mt-2">
          Sign in with a verified account for {emails.join(", ")} to access
          Admin.
        </p>
      ) : (
        <p className="mt-2">
          Choose an owner email in the app's access configuration before launch.
        </p>
      )}
      <p className="mt-2 text-[var(--color-text-muted)]">
        Open your app and sign in with this account. If you need an account, use
        the account setup option on its sign-in screen and verify your email.
        Then open Admin from your account menu. Your app's configured sign-in
        service manages your password and recovery.
      </p>
    </section>
  );
}
