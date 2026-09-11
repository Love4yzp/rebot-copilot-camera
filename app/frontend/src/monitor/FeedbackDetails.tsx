import { useEffect, useState } from "react";
import { api } from "../api";
import { useToast } from "../components/Toasts";
import type { SimulationState } from "../types";

export function FeedbackDetails({
  positions,
  simulated,
  teaching,
}: {
  positions: Record<string, number>;
  simulated: boolean;
  teaching: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [physics, setPhysics] = useState<SimulationState | null>(null);
  const { attempt } = useToast();
  useEffect(() => {
    if (!open || !simulated) return;
    let disposed = false;
    const poll = () =>
      api.simulation
        .state()
        .then((s) => {
          if (!disposed) setPhysics(s);
        })
        .catch(() => {
          if (!disposed) setPhysics(null);
        });
    void poll();
    const timer = setInterval(poll, 500);
    return () => {
      disposed = true;
      clearInterval(timer);
    };
  }, [open, simulated]);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>详细数据</summary>
      {simulated && (
        <p>
          负载：{physics?.payload ?? "未提供"} · 惯量：
          {physics?.inertia_source ?? "未提供"} · 仿真落后{" "}
          {physics ? (physics.wall_lag_s ?? 0).toFixed(3) + " s" : "未提供"}
        </p>
      )}
      <div className="joint-details">
        {Object.entries(positions).map(([name, q], index) => (
          <div key={name}>
            <span className="num">
              {name} · {q.toFixed(3)} rad
            </span>
            <span className="num">
              {simulated && physics?.error?.[index] !== undefined && (
                <>误差 {physics.error[index].toFixed(3)} rad · </>
              )}
              {simulated && physics?.tau?.[index] !== undefined
                ? physics.tau[index].toFixed(2) +
                  " N·m" +
                  (physics.saturated?.[index] ? " · 力矩饱和" : "")
                : "测量力矩未提供"}
            </span>
            {simulated && name !== "gripper" && (
              <span>
                <button
                  disabled={!teaching}
                  onClick={() =>
                    void attempt(() => api.simulation.perturb(name, -1))
                  }
                >
                  − 推动
                </button>
                <button
                  disabled={!teaching}
                  onClick={() =>
                    void attempt(() => api.simulation.perturb(name, 1))
                  }
                >
                  ＋ 推动
                </button>
              </span>
            )}
          </div>
        ))}
      </div>
      {simulated && (
        <p className="hint">
          先点「+ 录位姿」，再短时推动关节；每次外力 0.15
          秒。力矩为模型计算值，不是真机测量。
        </p>
      )}
    </details>
  );
}
