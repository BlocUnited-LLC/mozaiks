export function readJson(files, name) {
  try {
    const value = JSON.parse(files?.[name] || files?.[`app/${name}`] || '{}');
    return value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  } catch {
    return {};
  }
}

export default function OwnerAccessCard({ files, previewUrl = null }) {
  const manifest = readJson(files, 'app.json');
  const emails = Array.isArray(manifest.admins)
    ? manifest.admins.filter(email => typeof email === 'string' && email.trim()) : [];
  let ownerUrl = null;
  if (manifest.authRequired === true && previewUrl && emails.length) {
    try {
      const preview = new URL(previewUrl);
      const loopback = ['localhost', '127.0.0.1', '[::1]'].includes(preview.hostname);
      if ((preview.protocol === 'https:' || (preview.protocol === 'http:' && loopback)) && !preview.username && !preview.password) {
        ownerUrl = new URL('/?owner=1', preview.origin).href;
      }
    } catch { /* A pending preview has no owner-entry action. */ }
  }

  return (
    <section aria-label="App owner access" className="rounded-xl border border-border p-4 text-sm text-foreground">
      <h3 className="font-semibold">Owner access</h3>
      {emails.length ? <p className="mt-2">Draft owner email · {emails.join(', ')}</p>
        : <p role="status" className="mt-2">Set an owner email in app/app.json before setting up owner access.</p>}
      {ownerUrl && <a className="mt-3 inline-block rounded-lg border border-primary px-3 py-2 font-semibold"
        href={ownerUrl} target="_blank" rel="noopener noreferrer">Open as owner</a>}
      <p className="mt-3">The running preview may differ from this draft. It selects its sign-in service and checks your Admin access. A shared sign-in session can take you straight through.</p>
      <details className="mt-3">
        <summary className="cursor-pointer font-semibold">Set up a self-hosted app</summary>
        <ol className="mt-2 list-decimal space-y-2 pl-5">
          <li>Configure your OIDC provider using the exported environment settings and register the app's callback URL.</li>
          <li>Create or invite the owner account through that provider, choose its password there, and verify its email.</li>
          <li>Open the app's sign-in route from app/config/auth.yaml with ?owner=1 to confirm Admin access.</li>
        </ol>
      </details>
    </section>
  );
}
