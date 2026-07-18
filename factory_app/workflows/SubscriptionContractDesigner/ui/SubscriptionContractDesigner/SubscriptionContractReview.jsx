import { useMemo, useState } from 'react';
import { Button, Panel, StatusPill } from '@mozaiks/chat-ui/ui';

function asList(value) {
  return Array.isArray(value) ? value : [];
}

function TextList({ items, empty = 'None declared.' }) {
  const values = asList(items).filter(Boolean);
  if (!values.length) {
    return <p className="text-xs text-muted-foreground">{empty}</p>;
  }
  return (
    <ul className="flex flex-col gap-1">
      {values.map((item, index) => (
        <li key={`${item}-${index}`} className="text-xs text-foreground">
          {String(item)}
        </li>
      ))}
    </ul>
  );
}

function Section({ title, children }) {
  return (
    <section className="rounded-lg border border-border/60 bg-muted/20 p-4">
      <h3 className="mb-3 text-xs font-semibold uppercase text-muted-foreground">
        {title}
      </h3>
      {children}
    </section>
  );
}

function PlanCard({ plan }) {
  const capabilities = asList(plan?.capabilities);
  const limits = asList(plan?.usage_limits);
  const allowances = asList(plan?.token_allowances);

  return (
    <div className="rounded-lg border border-border/60 bg-background/60 p-4">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-sm font-semibold text-foreground">
            {plan?.label || plan?.plan_id || 'Plan'}
          </h4>
          {plan?.plan_id && (
            <p className="font-mono text-xs text-muted-foreground">{plan.plan_id}</p>
          )}
        </div>
        {plan?.plan_id && <StatusPill tone="default">{plan.plan_id}</StatusPill>}
      </div>

      {plan?.description && (
        <p className="mb-3 text-sm text-muted-foreground">{plan.description}</p>
      )}

      <div className="grid gap-3 md:grid-cols-3">
        <div>
          <p className="mb-1 text-xs font-semibold text-muted-foreground">Capabilities</p>
          <TextList items={capabilities} />
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-muted-foreground">Usage Limits</p>
          {limits.length ? (
            <ul className="flex flex-col gap-1">
              {limits.map((limit, index) => (
                <li key={`${limit?.meter_id || index}`} className="text-xs text-foreground">
                  <span className="font-mono">{limit?.meter_id}</span>
                  {limit?.monthly_limit != null && `: ${limit.monthly_limit} / month`}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">None declared.</p>
          )}
        </div>
        <div>
          <p className="mb-1 text-xs font-semibold text-muted-foreground">Token Allowances</p>
          {allowances.length ? (
            <ul className="flex flex-col gap-1">
              {allowances.map((allowance, index) => (
                <li key={`${allowance?.wallet_id || index}`} className="text-xs text-foreground">
                  <span className="font-mono">{allowance?.wallet_id}</span>
                  {allowance?.amount != null && `: ${allowance.amount}`}
                  {allowance?.cadence && ` / ${allowance.cadence}`}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-muted-foreground">None declared.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function Metric({ label, value }) {
  return (
    <div className="rounded-lg border border-border/60 bg-background/60 p-3">
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-1 text-lg font-semibold text-foreground">{value}</p>
    </div>
  );
}

function RationaleTable({ rows }) {
  const items = asList(rows);
  if (!items.length) {
    return <p className="text-xs text-muted-foreground">No rationale entries were provided.</p>;
  }
  return (
    <div className="flex flex-col divide-y divide-border/50 rounded-lg border border-border/50">
      {items.map((row, index) => (
        <div key={index} className="grid gap-3 p-3 md:grid-cols-[160px_1fr_1fr]">
          <div className="font-mono text-xs text-muted-foreground">
            {row?.source_context || 'source'}
          </div>
          <p className="text-xs text-foreground">{row?.signal || 'No source signal.'}</p>
          <p className="text-xs text-foreground">{row?.decision || 'No decision.'}</p>
        </div>
      ))}
    </div>
  );
}

function UpdateList({ updates }) {
  const items = asList(updates);
  if (!items.length) {
    return <p className="text-xs text-muted-foreground">No gated module actions declared.</p>;
  }
  return (
    <ul className="flex flex-col gap-2">
      {items.map((update, index) => (
        <li key={index} className="rounded-md border border-border/50 bg-background/60 p-3 text-xs">
          <span className="font-mono text-foreground">{update?.module_id}.{update?.action_id}</span>
          <span className="mx-2 text-muted-foreground">requires</span>
          <span className="font-mono text-primary">{update?.entitlement_gate || 'no gate'}</span>
        </li>
      ))}
    </ul>
  );
}

export default function SubscriptionContractReview({ payload = {}, onResponse }) {
  const [confirmed, setConfirmed] = useState(false);
  const [changeText, setChangeText] = useState('');
  const [submitted, setSubmitted] = useState(null);

  const plans = asList(payload.plans);
  const tokenWallets = asList(payload.token_wallets);
  const topUps = asList(payload.top_up_products);
  const usagePolicies = asList(payload.usage_charge_policies);
  const moduleUpdates = asList(payload.module_contract_updates);
  const workflowUpdates = asList(payload.workflow_contract_updates);
  const pages = asList(payload.page_surface_requirements);
  const generatedFiles = asList(payload.generated_files);
  const forbiddenOutputs = asList(payload.forbidden_outputs);
  const contractRequired = Boolean(payload.contract_required);
  const canRequestChanges = changeText.trim().length > 0 && submitted !== 'changes_requested';

  const summary = useMemo(() => {
    if (!contractRequired) {
      return 'No app-owned subscription contract is required for this build.';
    }
    return payload.review_boundary?.summary || (
      'This confirms the provider-neutral subscription contract for downstream app generation.'
    );
  }, [contractRequired, payload.review_boundary]);

  function confirmContract() {
    setSubmitted('confirmed');
    onResponse?.({
      action: 'confirm',
      approved: true,
      status: 'approved',
    });
  }

  function requestChanges() {
    const requestedChanges = changeText.trim();
    setSubmitted('changes_requested');
    onResponse?.({
      action: 'request_changes',
      approved: false,
      status: 'changes_requested',
      requested_changes: requestedChanges,
    });
  }

  return (
    <Panel className="max-w-5xl">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="mb-1 text-xs font-semibold uppercase text-muted-foreground">
            Subscription Plan Review
          </p>
          <h2 className="text-xl font-semibold text-foreground">
            {payload.app_name || payload.app_id || 'Generated App'}
          </h2>
          {payload.rationale && (
            <p className="mt-2 max-w-3xl text-sm text-muted-foreground">
              {payload.rationale}
            </p>
          )}
        </div>
        <StatusPill tone={contractRequired ? 'warning' : 'success'}>
          {contractRequired ? 'Contract Required' : 'No Contract Needed'}
        </StatusPill>
      </div>

      <div className="mb-4 rounded-lg border border-warning/40 bg-warning/10 px-4 py-3 text-sm text-warning">
        {summary}
      </div>

      <div className="mb-4 grid gap-3 md:grid-cols-4">
        <Metric label="Plans" value={plans.length} />
        <Metric label="Token Wallets" value={tokenWallets.length} />
        <Metric label="Gated Actions" value={moduleUpdates.length} />
        <Metric label="Generated Files" value={generatedFiles.length} />
      </div>

      {contractRequired ? (
        <div className="flex flex-col gap-4">
          <Section title="Subscription Plans">
            <div className="grid gap-3">
              {plans.map((plan, index) => (
                <PlanCard key={plan?.plan_id || index} plan={plan} />
              ))}
            </div>
          </Section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Token Wallets">
              {tokenWallets.length ? (
                <ul className="flex flex-col gap-2">
                  {tokenWallets.map((wallet, index) => (
                    <li key={wallet?.wallet_id || index} className="rounded-md border border-border/50 bg-background/60 p-3 text-xs">
                      <p className="font-mono text-foreground">{wallet?.wallet_id}</p>
                      <p className="text-muted-foreground">
                        {wallet?.unit || 'tokens'} / {wallet?.scope || 'user'}
                      </p>
                      {wallet?.depleted_balance?.billing_route && (
                        <p className="mt-1 text-muted-foreground">
                          Depleted route: <span className="font-mono">{wallet.depleted_balance.billing_route}</span>
                        </p>
                      )}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-muted-foreground">This is access-only SaaS.</p>
              )}
            </Section>

            <Section title="Top-Ups And Usage Charges">
              <p className="mb-2 text-xs font-semibold text-muted-foreground">Top-up products</p>
              {topUps.length ? (
                <ul className="mb-3 flex flex-col gap-1">
                  {topUps.map((product, index) => (
                    <li key={product?.product_id || index} className="text-xs text-foreground">
                      <span className="font-mono">{product?.product_id}</span>
                      {product?.token_amount != null && `: ${product.token_amount} tokens`}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="mb-3 text-xs text-muted-foreground">None declared.</p>
              )}
              <p className="mb-2 text-xs font-semibold text-muted-foreground">Usage charge policies</p>
              <TextList
                items={usagePolicies.map((policy) => `${policy?.meter_id || 'meter'}: ${policy?.basis || 'basis'}`)}
              />
            </Section>
          </div>

          <Section title="Entitlement And Workflow Updates">
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <p className="mb-2 text-xs font-semibold text-muted-foreground">Module action gates</p>
                <UpdateList updates={moduleUpdates} />
              </div>
              <div>
                <p className="mb-2 text-xs font-semibold text-muted-foreground">Workflow metering</p>
                <TextList
                  items={workflowUpdates.map((update) => `${update?.workflow_name}: ${update?.capability_id}`)}
                  empty="No workflow-level metering declared."
                />
              </div>
            </div>
          </Section>

          <Section title="Traceable Plan Reasoning">
            <RationaleTable rows={payload.plan_design_rationale} />
          </Section>

          <Section title="Generated App Surfaces">
            <TextList
              items={pages.map((page) => `${page?.route || page?.page_id}: ${page?.purpose || 'usage surface'}`)}
              empty="No additional page requirements declared."
            />
          </Section>

          {payload.yaml_preview && (
            <Section title="subscriptions.yaml Preview">
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-border/50 bg-background/80 p-3 font-mono text-xs text-foreground">
                {payload.yaml_preview}
              </pre>
            </Section>
          )}
        </div>
      ) : (
        <Section title="No Subscription Contract">
          <p className="text-sm text-muted-foreground">
            Downstream generation should not create subscription settings, billing facades,
            token wallets, entitlement gates, or usage-metered workflow changes for this build.
          </p>
        </Section>
      )}

      {forbiddenOutputs.length > 0 && (
        <Section title="Guardrails">
          <TextList items={forbiddenOutputs} />
        </Section>
      )}

      <div className="mt-4 rounded-lg border border-border/60 bg-background/60 p-4">
        <label className="flex items-start gap-3 text-sm text-foreground">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            className="mt-1 accent-primary"
            data-testid="confirm-subscription-contract-checkbox"
          />
          <span>
            Yes, this subscription plan contract matches what the user wants.
            I understand this only approves provider-neutral generated app settings;
            it does not charge anyone, assign customers, or credit tokens.
          </span>
        </label>

        <div className="mt-4 flex flex-col gap-3">
          <Button
            variant="primary"
            disabled={!confirmed || submitted === 'confirmed'}
            onClick={confirmContract}
            data-testid="confirm-subscription-contract-cta"
          >
            {submitted === 'confirmed' ? 'Confirmed' : 'Confirm Subscription Plan Contract'}
          </Button>

          <textarea
            value={changeText}
            onChange={(event) => setChangeText(event.target.value)}
            placeholder="Describe what should change before continuing."
            rows={3}
            className="min-h-[84px] rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-primary"
            data-testid="subscription-contract-change-request"
          />
          <Button
            variant="secondary"
            disabled={!canRequestChanges}
            onClick={requestChanges}
            data-testid="request-subscription-contract-changes-cta"
          >
            {submitted === 'changes_requested' ? 'Changes Requested' : 'Request Changes'}
          </Button>
        </div>
      </div>
    </Panel>
  );
}
