const ThemePreviewCard = ({ payload = {} }) => {
  const {
    title = "Captured Theme",
    message = "",
    appearance = "system",
    primaryColor = "#06b6d4",
    secondaryColor = "#8b5cf6",
    accentColor = "#f59e0b",
    backgroundColor = "#0b1220",
    surfaceColor = "#0f1724",
    bodyFont = "system-ui",
    headingFont = "system-ui",
    variant = "modern",
    radius = "medium",
  } = payload;

  const radiusMap = { none: "0", small: "4px", medium: "8px", large: "16px" };
  const borderRadius = radiusMap[radius] || "8px";

  return (
    <div className="rounded-lg border border-border bg-card p-5 space-y-4">
      <h3 className="text-lg font-semibold text-foreground">{title}</h3>
      {message && (
        <p className="text-sm text-muted-foreground">{message}</p>
      )}

      {/* Color swatches */}
      <div className="space-y-2">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Palette
        </span>
        <div className="flex gap-2">
          {[
            { label: "Primary", color: primaryColor },
            { label: "Secondary", color: secondaryColor },
            { label: "Accent", color: accentColor },
            { label: "Background", color: backgroundColor },
            { label: "Surface", color: surfaceColor },
          ].map(({ label, color }) => (
            <div key={label} className="flex flex-col items-center gap-1">
              <div
                className="w-10 h-10 border border-border"
                style={{ backgroundColor: color, borderRadius }}
              />
              <span className="text-xs text-muted-foreground">{label}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Typography */}
      <div className="space-y-2">
        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
          Typography
        </span>
        <div className="flex gap-6">
          <div>
            <span className="text-xs text-muted-foreground">Body</span>
            <p className="text-sm text-foreground" style={{ fontFamily: bodyFont }}>
              {bodyFont}
            </p>
          </div>
          <div>
            <span className="text-xs text-muted-foreground">Heading</span>
            <p className="text-sm text-foreground font-bold" style={{ fontFamily: headingFont }}>
              {headingFont}
            </p>
          </div>
        </div>
      </div>

      {/* Meta */}
      <div className="flex gap-4 pt-2 border-t border-border">
        <div className="flex items-center gap-1.5">
          <div className={`w-2 h-2 rounded-full ${appearance === "dark" ? "bg-foreground" : "bg-muted"}`} />
          <span className="text-xs text-muted-foreground capitalize">{appearance}</span>
        </div>
        <div>
          <span className="text-xs text-muted-foreground capitalize">{variant}</span>
        </div>
        <div>
          <span className="text-xs text-muted-foreground">Radius: {radius}</span>
        </div>
      </div>
    </div>
  );
};

export default ThemePreviewCard;
