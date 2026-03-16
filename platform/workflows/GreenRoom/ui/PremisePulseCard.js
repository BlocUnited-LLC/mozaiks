import React from "react";

const toText = (value, fallback = "Unknown") => {
  const text = typeof value === "string" ? value.trim() : "";
  return text || fallback;
};

export default function PremisePulseCard({ payload = {} }) {
  const tone = toText(payload.tone, "warm");
  const audience = toText(payload.audience, "general");
  const openingAngle = toText(payload.opening_angle, "No opening angle captured");
  const boundaryRule = toText(payload.boundary_rule, "No boundary rule captured");

  return (
    <section className="rounded-xl border border-cyan-600/40 bg-slate-950/85 p-4 text-slate-100 shadow-lg shadow-cyan-900/20">
      <header className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold uppercase tracking-wide text-cyan-300">
          Premise Pulse
        </h3>
        <span className="rounded bg-cyan-800/50 px-2 py-0.5 text-xs uppercase tracking-wide text-cyan-100">
          {tone}
        </span>
      </header>
      <dl className="space-y-2 text-sm">
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-400">Audience</dt>
          <dd className="text-slate-100">{audience}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-400">Opening Angle</dt>
          <dd className="text-slate-100">{openingAngle}</dd>
        </div>
        <div>
          <dt className="text-xs uppercase tracking-wide text-slate-400">Boundary Rule</dt>
          <dd className="text-slate-100">{boundaryRule}</dd>
        </div>
      </dl>
    </section>
  );
}
