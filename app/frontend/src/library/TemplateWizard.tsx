import { useEffect, useMemo, useState } from "react";
import { api, ApiError } from "../api";
import type { Block, Pose, ProviderInfo, SeqTemplate, Sequence } from "../types";
import { markerIcon, markerLabel } from "../timeline/markers";
import { useToast } from "../components/Toasts";

interface Props {
  template: SeqTemplate;
  poses: Pose[];
  providers: ProviderInfo[];
  /** Live joint angles from the control loop, shown while recording. */
  positions: Record<string, number>;
  latched: boolean;
  executing: boolean;
  /** A pose was captured mid-wizard — the parent refetches the library. */
  onPosesChanged: () => void;
  onClose: () => void;
  onCreated: (sequence: Sequence) => void;
}

type Mode = "idle" | "teaching" | "picking" | "finish";

type Hold = Extract<Block, { type: "hold" }>;

/**
 * Instantiate a template as a guided walk down its stations — a bottom rail,
 * not a dialog. Teaching is the one flow where the operator is looking at the
 * arm with both hands on it; a centred modal covers the 3D view and puts the
 * instruction where nobody is looking (the old teach modal's mistake, see
 * TeachBar). The rail replaces the transport while open and never covers the
 * estop bar.
 *
 * Per station the operator either records a fresh pose (teach mode, drag the
 * arm, save) or binds an existing library pose — optionally sending the arm
 * there first to check the framing. Teach mode is entered and left *per
 * station*, not held for the wizard's whole life: between stations the
 * operator is reading the screen, and the arm should be holding itself then,
 * not floating.
 *
 * Escape is deliberately NOT bound here. It is the emergency stop's shortcut
 * (a window-level listener in EstopBar), and mid-teach that binding is the
 * safety path — a rail is not a modal, it does not get to take that key.
 * Exiting is the rail's own button.
 */
export function TemplateWizard({
  template,
  poses,
  providers,
  positions,
  latched,
  executing,
  onPosesChanged,
  onClose,
  onCreated,
}: Props) {
  const { attempt, show } = useToast();
  const stations = useMemo<Hold[]>(
    () => template.recipe.filter((b): b is Hold => b.type === "hold"),
    [template],
  );
  const [bound, setBound] = useState<(string | null)[]>(() => stations.map(() => null));
  const [current, setCurrent] = useState(0);
  const [mode, setMode] = useState<Mode>("idle");
  const [poseName, setPoseName] = useState("");
  const [seqName, setSeqName] = useState(`${template.name} · 副本`);
  const [saving, setSaving] = useState(false);

  // Both directions must be idempotent — StrictMode mounts effects twice, and
  // repeating teach(false) is harmless.
  useEffect(() => {
    if (mode !== "teaching") return;
    void attempt(() => api.teach(true));
    return () => {
      void api.teach(false).catch(() => {});
    };
  }, [mode, attempt]);

  // An estop mid-record exits recording but keeps the wizard and every bound
  // station: the dangerous half (a floating arm against a latched stop) ends
  // with teach mode; the operator's work is not the panic button's to throw
  // away.
  useEffect(() => {
    if (latched && mode === "teaching") {
      setMode("idle");
      show("info", "已急停 — 已退出录制，向导进度保留");
    }
  }, [latched, mode, show]);

  const poseNameOf = (id: string | null) => poses.find((p) => p.id === id)?.name ?? null;

  const stationSummary = (station: Hold): string => {
    const acts = station.markers
      .map((m) => `${markerIcon(m.kind)}${markerLabel(m.kind, providers)}`)
      .join(" ");
    return `停 ${station.duration_s.toFixed(1)}s${acts ? ` · ${acts}` : ""}`;
  };

  const bind = (poseId: string) => {
    const next = bound.map((v, i) => (i === current ? poseId : v));
    setBound(next);
    const open = next.findIndex((v) => v === null);
    if (open === -1) {
      setMode("finish");
    } else {
      setCurrent(open);
      setMode("idle");
    }
  };

  /** Jump back to any station to review or re-bind it. Recording must be
   * finished or cancelled first — teach mode exits only through its verbs. */
  const jumpTo = (i: number) => {
    if (mode === "teaching" || saving) return;
    setCurrent(i);
    setMode("idle");
  };

  const startTeach = () => {
    setPoseName(`${template.name} · 站位 ${current + 1}`);
    setMode("teaching");
  };

  const save = async () => {
    const name = poseName.trim() || `${template.name} · 站位 ${current + 1}`;
    setSaving(true);
    const pose = await attempt(() => api.poses.capture(name), `已录位姿「${name}」`);
    setSaving(false);
    if (!pose) return; // toast already shown; stay in teaching
    onPosesChanged();
    bind(pose.id); // leaving "teaching" exits teach mode via the effect
  };

  const gotoPose = async (pose: Pose) => {
    try {
      await api.poses.goto(pose.id);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        show("info", latched ? "已急停 — 先解除急停" : "机械臂正忙 — 等当前动作完成");
      } else {
        show("error", error instanceof Error ? error.message : String(error));
      }
    }
  };

  const create = async () => {
    const name = seqName.trim();
    if (!name || bound.some((v) => v === null)) return;
    setSaving(true);
    const sequence = await attempt(() =>
      api.templates.instantiate(template.id, { name, pose_ids: bound as string[] }),
    );
    setSaving(false);
    if (sequence) onCreated(sequence);
  };

  const station = stations[current];
  const boundName = poseNameOf(bound[current]);
  const joints = Object.entries(positions);

  const instruction = (): string => {
    const where = `站位 ${current + 1}/${stations.length} · ${stationSummary(station)}`;
    if (mode === "teaching") return `${where} — 臂已卸力，可以直接推。到位后命名并保存。`;
    if (mode === "picking") return `${where} — 选一个已有位姿；「去这里」先把臂开过去确认。`;
    if (mode === "finish") return "全部站位已绑定。命名后生成普通序列 —— 复印即脱钩，与模板两不相干。";
    return boundName
      ? `${where} — 已绑定「${boundName}」，可重录或改选。`
      : `${where} — 把机械臂推到这一站录下来，或从素材库选一个。`;
  };

  return (
    <div className="teach-bar wizard" role="region" aria-label="模板向导">
      {mode === "teaching" ? (
        <button
          type="button"
          className="teach-bar__estop"
          onClick={() => attempt(() => api.estop.engage("operator pressed stop during teach"))}
        >
          急停
        </button>
      ) : null}

      <div className="wizard__progress">
        <span className="wizard__tpl" title={template.name}>
          {template.name}
        </span>
        <div className="wizard__dots">
          {stations.map((_, i) => (
            <button
              key={i}
              type="button"
              className={`wizard__dot ${bound[i] ? "bound" : ""} ${i === current ? "current" : ""}`}
              aria-label={`站位 ${i + 1}${bound[i] ? `，已绑定 ${poseNameOf(bound[i]) ?? ""}` : "，未绑定"}`}
              onClick={() => jumpTo(i)}
            >
              {i + 1}
            </button>
          ))}
        </div>
      </div>

      <div className="teach-bar__body">
        <p className="teach-bar__step">{instruction()}</p>
        {mode === "teaching" ? (
          <div className="teach-bar__readout num" aria-label="实时姿态">
            {joints.map(([joint, value]) => (
              <span key={joint}>
                {joint.replace("joint", "J")} {value.toFixed(2)}
              </span>
            ))}
          </div>
        ) : null}
        {mode === "picking" ? (
          <div className="wizard__poses">
            {poses.length === 0 ? (
              <span className="hint">素材库还是空的 —— 用「拖臂录这一站」。</span>
            ) : (
              poses.map((pose) => (
                <span key={pose.id} className="wizard__pose">
                  <button type="button" onClick={() => bind(pose.id)}>
                    {pose.name}
                  </button>
                  <button
                    type="button"
                    className="wizard__goto"
                    disabled={latched || executing}
                    title={latched ? "已急停" : executing ? "执行中" : "把臂开到这个位姿"}
                    onClick={() => void gotoPose(pose)}
                  >
                    去这里
                  </button>
                </span>
              ))
            )}
          </div>
        ) : null}
        {mode === "finish" ? (
          <div className="wizard__review num">
            {stations.map((_, i) => (
              <span key={i}>
                {i + 1}. {poseNameOf(bound[i]) ?? "?"}
              </span>
            ))}
          </div>
        ) : null}
      </div>

      <div className="teach-bar__actions">
        {mode === "teaching" ? (
          <>
            <input
              value={poseName}
              aria-label="位姿名称"
              onChange={(event) => setPoseName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !saving) void save();
              }}
            />
            <button type="button" className="primary" disabled={saving} onClick={() => void save()}>
              {saving ? "保存中…" : "保存位姿"}
            </button>
            <button type="button" className="ghost" disabled={saving} onClick={() => setMode("idle")}>
              取消录制
            </button>
          </>
        ) : mode === "picking" ? (
          <button type="button" className="ghost" onClick={() => setMode("idle")}>
            返回
          </button>
        ) : mode === "finish" ? (
          <>
            <input
              value={seqName}
              aria-label="序列名称"
              onChange={(event) => setSeqName(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !saving) void create();
              }}
            />
            <button
              type="button"
              className="primary"
              disabled={!seqName.trim() || saving}
              onClick={() => void create()}
            >
              {saving ? "生成中…" : "生成序列"}
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              className="primary"
              disabled={latched}
              title={latched ? "已急停 — 先解除急停" : "卸力进入示教，把机械臂推到这一站"}
              onClick={startTeach}
            >
              推臂录这一站
            </button>
            <button type="button" onClick={() => setMode("picking")}>
              选已有位姿
            </button>
          </>
        )}
        {mode !== "teaching" ? (
          <button type="button" className="ghost" onClick={onClose}>
            退出向导
          </button>
        ) : null}
      </div>
    </div>
  );
}
