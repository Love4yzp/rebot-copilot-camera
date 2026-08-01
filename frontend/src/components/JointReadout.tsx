interface Props {
  positions: Record<string, number>;
  velocities: Record<string, number>;
}

/** Rough display range. The real limits live in the URDF and are enforced
 *  server-side; this is only to give the bars a sensible scale. */
const SPAN = Math.PI;

export function JointReadout({ positions, velocities }: Props) {
  const names = Object.keys(positions);
  if (names.length === 0) return null;

  return (
    <div className="joint-readout">
      {names.map((name) => {
        const value = positions[name] ?? 0;
        const speed = velocities[name] ?? 0;
        const fraction = Math.max(0, Math.min(1, (value + SPAN) / (2 * SPAN)));
        const moving = Math.abs(speed) > 0.01;

        return (
          <div className="row" key={name}>
            <span style={{ color: "var(--text-dim)" }}>{name.replace("joint", "J")}</span>
            <span className="bar">
              <span
                style={{
                  left: `${Math.min(fraction, 0.5) * 100}%`,
                  width: `${Math.abs(fraction - 0.5) * 100}%`,
                  background: moving ? "var(--accent)" : "var(--accent-dim)",
                }}
              />
            </span>
            <span style={{ color: moving ? "var(--text)" : "var(--text-dim)" }}>
              {value >= 0 ? " " : ""}
              {value.toFixed(3)}
            </span>
          </div>
        );
      })}
    </div>
  );
}
