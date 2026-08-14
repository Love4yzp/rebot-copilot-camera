/**
 * Tuning panel — Tweakpane-based parameter editor for the arm's live tuning
 * config (payload profile, float gains, floatlock thresholds, settle, approach).
 *
 * Docked in the layout flow on the right of the 3D view, not a floating overlay.
 * Uses grayscale only — the app's four colours (red/amber/green/white) are
 * reserved for machine state, never for decoration.
 *
 * Prod gate: in prod mode the first open shows a blocking confirmation layer
 * (z-index 55, below the estop bar's 60) in the same style as ModeWarning.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Pane } from "tweakpane";
import type { TuningState } from "../types";
import { api, ApiError } from "../api";

interface Props {
  visible: boolean;
  appMode: "sim" | "prod" | null;
  onClose: () => void;
}

// ── field → section mapping ──────────────────────────────────────────────────
const FIELD_SECTION: Record<string, string> = {
  profile: "payload",
  cameraMass: "payload",
  comX: "payload",
  comY: "payload",
  comZ: "payload",
  kp: "float",
  kd: "float",
  linear_threshold: "floatlock",
  angular_threshold: "floatlock",
  release_factor: "floatlock",
  lock_factor: "floatlock",
  min_still_s: "floatlock",
  drift_rad: "settle",
  min_s: "settle",
  first_max_speed: "approach",
  g2s: "gravity",
  g2b: "gravity",
  g3s: "gravity",
  g3b: "gravity",
  g4s: "gravity",
  g4b: "gravity",
  g5s: "gravity",
  g5b: "gravity",
};

const SECTION_ORDER = ["payload", "float", "floatlock", "settle", "approach", "gravity"];

const SECTION_LABELS: Record<string, string> = {
  payload: "负载",
  float: "浮动手感",
  floatlock: "浮动/锁定",
  settle: "到位判定",
  approach: "进站",
  gravity: "重力修正",
};

// ── param holders (mutated by Tweakpane, read by change handlers) ────────────
interface Params {
  profile: string;
  /** The wire model allows null (unset); the slider shows that as 0. */
  cameraMass: number;
  comX: number;
  comY: number;
  comZ: number;
  kp: number;
  kd: number;
  linear_threshold: number;
  angular_threshold: number;
  release_factor: number;
  lock_factor: number;
  min_still_s: number;
  drift_rad: number;
  min_s: number;
  first_max_speed: number;
  g2s: number;
  g2b: number;
  g3s: number;
  g3b: number;
  g4s: number;
  g4b: number;
  g5s: number;
  g5b: number;
}

function defaultsFromState(state: TuningState): Params {
  const c = state.current;
  return {
    profile: c.payload.profile,
    cameraMass: c.payload.camera.mass ?? 0,
    comX: c.payload.camera.com[0],
    comY: c.payload.camera.com[1],
    comZ: c.payload.camera.com[2],
    kp: c.float.kp,
    kd: c.float.kd,
    linear_threshold: c.floatlock.linear_threshold,
    angular_threshold: c.floatlock.angular_threshold,
    release_factor: c.floatlock.release_factor,
    lock_factor: c.floatlock.lock_factor,
    min_still_s: c.floatlock.min_still_s,
    drift_rad: c.settle.drift_rad,
    min_s: c.settle.min_s,
    first_max_speed: c.approach.first_max_speed,
    g2s: c.gravity.scale.joint2 ?? 1,
    g2b: c.gravity.bias.joint2 ?? 0,
    g3s: c.gravity.scale.joint3 ?? 1,
    g3b: c.gravity.bias.joint3 ?? 0,
    g4s: c.gravity.scale.joint4 ?? 1,
    g4b: c.gravity.bias.joint4 ?? 0,
    g5s: c.gravity.scale.joint5 ?? 1,
    g5b: c.gravity.bias.joint5 ?? 0,
  };
}

export function TuningPanel({ visible, appMode, onClose }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const paneRef = useRef<Pane | null>(null);
  const paramsRef = useRef<Params | null>(null);
  const foldersRef = useRef<Record<string, { title: string | undefined }>>({});
  const [tuningState, setTuningState] = useState<TuningState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [prodConfirmed, setProdConfirmed] = useState(false);

  const showProdGate = visible && appMode === "prod" && !prodConfirmed;
  /** Flips false → true exactly once per load; stable across later updates. */
  const loaded = tuningState !== null;

  // Mirror for effects that must read the state without depending on it.
  const stateRef = useRef<TuningState | null>(null);
  stateRef.current = tuningState;

  // Fetch when the panel opens (and again once the prod gate is acknowledged).
  useEffect(() => {
    if (!visible || showProdGate) return;
    setError(null);
    api.tuning.get().then(setTuningState).catch(() => setError("无法加载调参状态"));
  }, [visible, showProdGate]);

  // Push server state into the live pane: mutate the params object, mark dirty
  // folders, repaint. The pane itself is created once and survives — recreating
  // it on every answer would reset folder collapse and scroll on each change.
  const applyState = useCallback((state: TuningState) => {
    setTuningState(state);
    const p = paramsRef.current;
    if (p) {
      const c = state.current;
      p.profile = c.payload.profile;
      p.cameraMass = c.payload.camera.mass ?? 0;
      p.comX = c.payload.camera.com[0];
      p.comY = c.payload.camera.com[1];
      p.comZ = c.payload.camera.com[2];
      p.kp = c.float.kp;
      p.kd = c.float.kd;
      p.linear_threshold = c.floatlock.linear_threshold;
      p.angular_threshold = c.floatlock.angular_threshold;
      p.release_factor = c.floatlock.release_factor;
      p.lock_factor = c.floatlock.lock_factor;
      p.min_still_s = c.floatlock.min_still_s;
      p.drift_rad = c.settle.drift_rad;
      p.min_s = c.settle.min_s;
      p.first_max_speed = c.approach.first_max_speed;
      p.g2s = c.gravity.scale.joint2 ?? 1;
      p.g2b = c.gravity.bias.joint2 ?? 0;
      p.g3s = c.gravity.scale.joint3 ?? 1;
      p.g3b = c.gravity.bias.joint3 ?? 0;
      p.g4s = c.gravity.scale.joint4 ?? 1;
      p.g4b = c.gravity.bias.joint4 ?? 0;
      p.g5s = c.gravity.scale.joint5 ?? 1;
      p.g5b = c.gravity.bias.joint5 ?? 0;
    }
    const dirty = new Set(state.dirty);
    for (const section of SECTION_ORDER) {
      const folder = foldersRef.current[section];
      if (folder) {
        const label = SECTION_LABELS[section];
        folder.title = dirty.has(section) ? `${label} ● 未保存` : label;
      }
    }
    paneRef.current?.refresh();
  }, []);

  // One changed field → one minimal patch. On 409/422 the server's own words
  // go to the error line and the blade is reverted by refetching.
  const handleChange = useCallback(
    (key: keyof Params, value: unknown) => {
      setError(null);
      const section = FIELD_SECTION[key];
      if (!section) return;

      let patch: Record<string, unknown>;
      if (key === "comX" || key === "comY" || key === "comZ") {
        // camera.com is one tuple on the wire — send all three on any change.
        const p = paramsRef.current;
        if (!p) return;
        patch = { payload: { camera: { com: [p.comX, p.comY, p.comZ] } } };
      } else if (key === "cameraMass") {
        patch = { payload: { camera: { mass: value as number } } };
      } else if (key === "profile") {
        patch = { payload: { profile: value as string } };
      } else if (key.startsWith("g")) {
        // The merge replaces the whole scale/bias dicts, so send all joints.
        const p = paramsRef.current;
        if (!p) return;
        patch = {
          gravity: {
            scale: { joint2: p.g2s, joint3: p.g3s, joint4: p.g4s, joint5: p.g5s },
            bias: { joint2: p.g2b, joint3: p.g3b, joint4: p.g4b, joint5: p.g5b },
          },
        };
      } else {
        patch = { [section]: { [key]: value } };
      }

      api.tuning
        .put(patch)
        .then(applyState)
        .catch((err) => {
          setError(err instanceof ApiError ? err.message : String(err));
          api.tuning.get().then(applyState).catch(() => {});
        });
    },
    [applyState],
  );

  // Bindings are created once; route them to the latest handler via a ref.
  const handleChangeRef = useRef(handleChange);
  handleChangeRef.current = handleChange;

  // Create the pane once, when the first state answer arrives. Deps are all
  // booleans that stay put across later state updates, so the pane (and its
  // folder collapse / scroll) survives every PUT response.
  useEffect(() => {
    if (!visible || showProdGate || !loaded || !containerRef.current) return;
    const state = stateRef.current;
    if (!state) return;

    const container = containerRef.current;
    const pane = new Pane({ container, title: "调参" });
    paneRef.current = pane;

    const params = defaultsFromState(state);
    paramsRef.current = params;

    const on = (key: keyof Params) => (ev: { last: boolean; value: unknown }) => {
      if (ev.last) handleChangeRef.current(key, ev.value);
    };

    // ── 负载 ────────────────────────────────────────────────────────────────
    const payloadFolder = pane.addFolder({ title: "负载" });
    foldersRef.current.payload = payloadFolder;
    payloadFolder
      .addBinding(params, "profile", {
        view: "list",
        label: "配置",
        options: Object.fromEntries(state.payload_options.map((o) => [o, o])),
      })
      .on("change", on("profile"));
    payloadFolder
      .addBinding(params, "cameraMass", {
        label: "相机质量 (kg)",
        min: 0,
        max: 5,
        step: 0.01,
      })
      .on("change", on("cameraMass"));
    payloadFolder
      .addBinding(params, "comX", { label: "COM X (m)", min: -0.5, max: 0.5, step: 0.001 })
      .on("change", on("comX"));
    payloadFolder
      .addBinding(params, "comY", { label: "COM Y (m)", min: -0.5, max: 0.5, step: 0.001 })
      .on("change", on("comY"));
    payloadFolder
      .addBinding(params, "comZ", { label: "COM Z (m)", min: -0.5, max: 0.5, step: 0.001 })
      .on("change", on("comZ"));

    // ── 浮动手感 ────────────────────────────────────────────────────────────
    const floatFolder = pane.addFolder({ title: "浮动手感" });
    foldersRef.current.float = floatFolder;
    floatFolder
      .addBinding(params, "kp", { label: "KP", min: 0, max: 10, step: 0.1 })
      .on("change", on("kp"));
    floatFolder
      .addBinding(params, "kd", { label: "KD", min: 0, max: 10, step: 0.1 })
      .on("change", on("kd"));

    // ── 浮动/锁定 ───────────────────────────────────────────────────────────
    const floatlockFolder = pane.addFolder({ title: "浮动/锁定" });
    foldersRef.current.floatlock = floatlockFolder;
    floatlockFolder
      .addBinding(params, "linear_threshold", {
        label: "线性阈值 (m)",
        min: 0.005,
        max: 0.5,
        step: 0.001,
      })
      .on("change", on("linear_threshold"));
    floatlockFolder
      .addBinding(params, "angular_threshold", {
        label: "角度阈值 (rad)",
        min: 0.01,
        max: 1,
        step: 0.001,
      })
      .on("change", on("angular_threshold"));
    floatlockFolder
      .addBinding(params, "release_factor", { label: "释放因子", min: 0.1, max: 4, step: 0.01 })
      .on("change", on("release_factor"));
    floatlockFolder
      .addBinding(params, "lock_factor", { label: "锁定因子", min: 0.05, max: 1, step: 0.01 })
      .on("change", on("lock_factor"));
    floatlockFolder
      .addBinding(params, "min_still_s", { label: "最小静止 (s)", min: 0.05, max: 2, step: 0.01 })
      .on("change", on("min_still_s"));

    // ── 到位判定 ────────────────────────────────────────────────────────────
    const settleFolder = pane.addFolder({ title: "到位判定" });
    foldersRef.current.settle = settleFolder;
    settleFolder
      .addBinding(params, "drift_rad", {
        label: "漂移 (rad)",
        min: 0.0005,
        max: 0.05,
        step: 0.0005,
      })
      .on("change", on("drift_rad"));
    settleFolder
      .addBinding(params, "min_s", { label: "最短时间 (s)", min: 0.05, max: 2, step: 0.01 })
      .on("change", on("min_s"));

    // ── 进站 ────────────────────────────────────────────────────────────────
    const approachFolder = pane.addFolder({ title: "进站" });
    foldersRef.current.approach = approachFolder;
    approachFolder
      .addBinding(params, "first_max_speed", {
        label: "最大速度 (rad/s)",
        min: 0.05,
        max: 1,
        step: 0.01,
      })
      .on("change", on("first_max_speed"));

    // ── 重力修正 ────────────────────────────────────────────────────────────
    const gravityFolder = pane.addFolder({ title: "重力修正" });
    foldersRef.current.gravity = gravityFolder;
    const scaleBinding = (key: keyof Params, label: string) =>
      gravityFolder
        .addBinding(params, key, { label: `${label} 比例`, min: 0.2, max: 2, step: 0.01 })
        .on("change", on(key));
    const biasBinding = (key: keyof Params, label: string) =>
      gravityFolder
        .addBinding(params, key, { label: `${label} 偏置 N·m`, min: -5, max: 5, step: 0.05 })
        .on("change", on(key));
    scaleBinding("g2s", "J2");
    biasBinding("g2b", "J2");
    scaleBinding("g3s", "J3");
    biasBinding("g3b", "J3");
    scaleBinding("g4s", "J4");
    biasBinding("g4b", "J4");
    scaleBinding("g5s", "J5");
    biasBinding("g5b", "J5");

    // Initial dirty markers.
    const dirty = new Set(state.dirty);
    for (const section of SECTION_ORDER) {
      const folder = foldersRef.current[section];
      if (folder) {
        const label = SECTION_LABELS[section];
        folder.title = dirty.has(section) ? `${label} ● 未保存` : label;
      }
    }

    return () => {
      pane.dispose();
      paneRef.current = null;
      paramsRef.current = null;
      foldersRef.current = {};
    };
  }, [visible, showProdGate, loaded]);

  // ── save / reset ───────────────────────────────────────────────────────────
  const handleSave = useCallback(() => {
    setError(null);
    api.tuning.save().then(applyState).catch(() => setError("保存失败"));
  }, [applyState]);

  const handleReset = useCallback(() => {
    setError(null);
    api.tuning.reset().then(applyState).catch(() => setError("恢复失败"));
  }, [applyState]);

  if (!visible) return null;

  // ── prod confirmation gate ─────────────────────────────────────────────────
  if (showProdGate) {
    return (
      <div className="tuning-prod-gate">
        <div className="tuning-prod-gate__panel" role="alertdialog" aria-modal="true" aria-label="PROD 调参警告">
          <h2 className="tuning-prod-gate__title">
            <span className="tuning-prod-gate__icon" aria-hidden="true">⚠</span>
            现在处于 PROD 模式
          </h2>
          <p className="tuning-prod-gate__body">
            调参会实时改变机械臂的物理行为——浮动手感、到位判定、进站速度、负载模型。
            请确认安全后再继续。
          </p>
          <div className="tuning-prod-gate__actions">
            <button type="button" className="ghost" onClick={onClose}>
              取消
            </button>
            <button type="button" className="primary" onClick={() => setProdConfirmed(true)}>
              我已了解
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── panel body ─────────────────────────────────────────────────────────────
  return (
    <div className="tuning-panel">
      <div className="tuning-panel__header">
        <span className="tuning-panel__title">调参</span>
        <button type="button" className="tuning-panel__close" onClick={onClose} aria-label="关闭调参面板">
          ✕
        </button>
      </div>
      {error ? (
        <div className="tuning-panel__error" role="alert">
          {error}
        </div>
      ) : null}
      <div className="tuning-panel__hint">
          切到「相机」负载前，先填相机质量（kg）——没填不能切。
      </div>
      <div className="tuning-panel__pane" ref={containerRef} />
      <div className="tuning-panel__footer">
        <button type="button" className="ghost" onClick={handleSave}>
          保存到配置
        </button>
        <button type="button" className="ghost" onClick={handleReset}>
          恢复已保存
        </button>
      </div>
    </div>
  );
}
