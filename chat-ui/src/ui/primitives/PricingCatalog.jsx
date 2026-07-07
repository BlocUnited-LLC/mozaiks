import { useMemo, useState } from 'react';

import { Button } from './Button.jsx';
import { StatusPill } from './Surface.jsx';
import { cn } from '../lib/cn.js';

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function itemId(item) {
  return item?.id ?? item?.add_on_id ?? item?.slot_id ?? item?.plan_id ?? '';
}

function normalizeGroups(groups, plans) {
  if (Array.isArray(groups) && groups.length > 0) {
    return groups
      .filter(isRecord)
      .map((group) => ({
        ...group,
        group_id: group.group_id ?? group.id,
        plan_ids: Array.isArray(group.plan_ids) ? group.plan_ids : [],
        add_on_ids: Array.isArray(group.add_on_ids) ? group.add_on_ids : [],
      }))
      .filter((group) => group.group_id && group.label);
  }

  return [{
    group_id: 'plans',
    label: 'Plans',
    description: 'Compare available subscription plans.',
    kind: 'subscription',
    plan_ids: Array.isArray(plans) ? plans.map((plan) => plan.plan_id).filter(Boolean) : [],
    add_on_ids: [],
  }];
}

function formatPrice(plan) {
  return plan?.price_display || plan?.pricing?.display || plan?.price?.display || 'Custom';
}

function formatInterval(plan) {
  const interval = plan?.pricing?.interval || plan?.price?.interval || '';
  if (!interval || interval === 'custom' || String(formatPrice(plan)).includes('/')) return '';
  return `/${interval}`;
}

function usageLabel(plan) {
  return (
    plan?.managed_ai?.display ||
    plan?.usage_limits?.[0]?.monthly_limit_display ||
    plan?.usage_limits?.[0]?.label ||
    ''
  );
}

function PlanCard({ plan, currentPlanId, highlightedPlanId, actionLabel, onAction }) {
  const isCurrent = plan.plan_id && plan.plan_id === currentPlanId;
  const isHighlighted = plan.plan_id && plan.plan_id === highlightedPlanId;
  const highlights = Array.isArray(plan.highlights) ? plan.highlights.slice(0, 4) : [];
  const label = actionLabel || plan.cta_label || (isCurrent ? 'Current plan' : `Choose ${plan.label}`);

  return (
    <article
      className={cn(
        'flex min-h-[25rem] flex-col rounded-xl border bg-card/32 p-5 shadow-sm shadow-black/5',
        isHighlighted ? 'border-primary/55 ring-1 ring-primary/25' : 'border-border/45',
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-xl font-semibold text-foreground">{plan.label}</h3>
          {plan.description ? (
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{plan.description}</p>
          ) : null}
        </div>
        {isCurrent ? <StatusPill tone="success">Current</StatusPill> : null}
      </div>

      <div className="mt-5 flex items-end gap-1">
        <span className="text-4xl font-bold text-foreground">{formatPrice(plan)}</span>
        {formatInterval(plan) ? (
          <span className="pb-1 text-sm text-muted-foreground">{formatInterval(plan)}</span>
        ) : null}
      </div>

      {usageLabel(plan) ? (
        <div className="mt-4 rounded-lg border border-primary/25 bg-primary/8 px-3 py-2 text-sm font-semibold text-primary">
          {usageLabel(plan)}
        </div>
      ) : null}

      {highlights.length > 0 ? (
        <ul className="mt-5 space-y-2">
          {highlights.map((highlight) => (
            <li key={highlight} className="flex items-start gap-2 text-sm leading-5 text-muted-foreground">
              <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/80" />
              <span>{highlight}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {onAction ? (
        <Button
          className="mt-auto w-full"
          variant={isHighlighted ? 'primary' : 'secondary'}
          disabled={isCurrent}
          onClick={() => onAction(plan)}
        >
          {label}
        </Button>
      ) : null}
    </article>
  );
}

function AddOnCard({ addOn, actionLabel, onAction }) {
  const highlights = Array.isArray(addOn.highlights) ? addOn.highlights.slice(0, 3) : [];
  const price = addOn?.price?.display || addOn?.price_display || addOn?.display_price || '';

  return (
    <article className="flex min-h-[16rem] flex-col rounded-xl border border-border/45 bg-card/32 p-5 shadow-sm shadow-black/5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-foreground">{addOn.label}</h3>
          {addOn.description ? (
            <p className="mt-2 text-sm leading-6 text-muted-foreground">{addOn.description}</p>
          ) : null}
        </div>
        {price ? <div className="shrink-0 text-right text-xl font-bold text-foreground">{price}</div> : null}
      </div>

      {highlights.length > 0 ? (
        <ul className="mt-5 space-y-2">
          {highlights.map((highlight) => (
            <li key={highlight} className="flex items-start gap-2 text-sm leading-5 text-muted-foreground">
              <span className="mt-1 h-1.5 w-1.5 shrink-0 rounded-full bg-primary/80" />
              <span>{highlight}</span>
            </li>
          ))}
        </ul>
      ) : null}

      {onAction ? (
        <Button className="mt-auto w-full" variant="secondary" onClick={() => onAction(addOn)}>
          {actionLabel || addOn.cta_label || 'View option'}
        </Button>
      ) : null}
    </article>
  );
}

export function PricingCatalog({
  title,
  subtitle,
  plans = [],
  groups = [],
  add_ons = [],
  default_group_id,
  current_plan_id,
  highlighted_plan_id,
  plan_action_label,
  add_on_action_label,
  onPlanAction,
  onAddOnAction,
}) {
  const normalizedPlans = Array.isArray(plans) ? plans.filter(isRecord) : [];
  const normalizedAddOns = Array.isArray(add_ons) ? add_ons.filter(isRecord) : [];
  const normalizedGroups = useMemo(
    () => normalizeGroups(groups, normalizedPlans),
    [groups, normalizedPlans],
  );
  const initialGroupId = default_group_id || normalizedGroups[0]?.group_id || 'plans';
  const [activeGroupId, setActiveGroupId] = useState(initialGroupId);
  const activeGroup = normalizedGroups.find((group) => group.group_id === activeGroupId) || normalizedGroups[0];
  const planIdSet = new Set(activeGroup?.plan_ids || []);
  const addOnIdSet = new Set(activeGroup?.add_on_ids || []);
  const visiblePlans = planIdSet.size
    ? normalizedPlans.filter((plan) => planIdSet.has(plan.plan_id))
    : activeGroup?.kind === 'add_on'
      ? []
      : normalizedPlans;
  const visibleAddOns = addOnIdSet.size
    ? normalizedAddOns.filter((addOn) => addOnIdSet.has(itemId(addOn)))
    : activeGroup?.kind === 'add_on'
      ? normalizedAddOns
      : [];

  if (normalizedPlans.length === 0 && normalizedAddOns.length === 0) return null;

  return (
    <section className="space-y-5">
      {(title || subtitle) ? (
        <div className="max-w-3xl">
          {title ? <h2 className="text-2xl font-semibold text-foreground">{title}</h2> : null}
          {subtitle ? <p className="mt-2 text-sm leading-6 text-muted-foreground">{subtitle}</p> : null}
        </div>
      ) : null}

      {normalizedGroups.length > 1 ? (
        <div className="flex flex-wrap gap-2 rounded-xl border border-border/40 bg-card/20 p-1.5">
          {normalizedGroups.map((group) => {
            const active = group.group_id === activeGroup.group_id;
            return (
              <button
                key={group.group_id}
                type="button"
                onClick={() => setActiveGroupId(group.group_id)}
                className={cn(
                  'rounded-lg px-4 py-2 text-sm font-semibold transition',
                  active
                    ? 'bg-primary text-primary-foreground shadow-sm shadow-primary/20'
                    : 'text-muted-foreground hover:bg-card/45 hover:text-foreground',
                )}
              >
                {group.label}
              </button>
            );
          })}
        </div>
      ) : null}

      {activeGroup?.description ? (
        <p className="text-sm leading-6 text-muted-foreground">{activeGroup.description}</p>
      ) : null}

      {visiblePlans.length > 0 ? (
        <div className="grid gap-4 lg:grid-cols-3">
          {visiblePlans.map((plan) => (
            <PlanCard
              key={plan.plan_id}
              plan={plan}
              currentPlanId={current_plan_id}
              highlightedPlanId={highlighted_plan_id}
              actionLabel={plan_action_label}
              onAction={onPlanAction}
            />
          ))}
        </div>
      ) : null}

      {visibleAddOns.length > 0 ? (
        <div className="grid gap-4 md:grid-cols-2">
          {visibleAddOns.map((addOn) => (
            <AddOnCard
              key={itemId(addOn)}
              addOn={addOn}
              actionLabel={add_on_action_label}
              onAction={onAddOnAction}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

export default PricingCatalog;
