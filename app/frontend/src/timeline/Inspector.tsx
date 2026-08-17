import type { Block, Pose, ProviderInfo, Sequence } from "../types";
import { MIN_HOLD_S } from "./model";
import { markerLabel, WAIT_KIND } from "./markers";
import { EASINGS } from "./easing";
import type { Selection } from "./TimelineView";
import { ProviderFields } from "../components/ProviderFields";

interface Props {
  sequence: Sequence;
  poses: Pose[];
  providers: ProviderInfo[];
  selection: NonNullable<Selection>;
  executing: boolean;
  onPatch: (blocks: Block[]) => void;
  onClose: () => void;
}

const snap = (v: number, step: number) => Math.round(v / step) * step;
const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

/**
 * The inspector: edits whatever is selected on the timeline.
 *
 * It is deliberately the only place transition parameters live — transitions
 * cannot be added or deleted by hand (that is the physics talking), but their
 * duration and easing are legitimate edits. The 路径形态 section is a greyed
 * placeholder: designed, not promised — the backend cannot walk a cartesian
 * straight line yet.
 */
export function Inspector({
  sequence,
  poses,
  providers,
  selection,
  executing,
  onPatch,
  onClose,
}: Props) {
  const blocks = sequence.blocks;
  const replaceBlock = (id: string, next: Block) =>
    onPatch(blocks.map((b) => (b.id === id ? next : b)));

  const body = () => {
    if (selection.kind === "block") {
      const block = blocks.find((b) => b.id === selection.id);
      if (!block) return <p className="hint">所选块已不存在。</p>;

      if (block.type === "hold") {
        const pose = poses.find((p) => p.id === block.pose_id);
        return (
          <>
            <div className="insp__field">
              <span className="insp__label">位姿</span>
              <span className="insp__pose">
                {pose?.name ?? "已删除位姿"}
                <span className="insp__link">链接</span>
              </span>
            </div>
            {!pose ? (
              <p className="hint">库里的位姿已被删除 —— 执行到这块时臂保持当时姿态。</p>
            ) : null}
            <div className="insp__field">
              <span className="insp__label">时长</span>
              <Stepper
                value={block.duration_s}
                step={0.5}
                min={MIN_HOLD_S}
                max={60}
                unit="s"
                disabled={executing}
                onChange={(v) => replaceBlock(block.id, { ...block, duration_s: v })}
              />
            </div>
            <p className="hint">保持块只存位姿链接，不存关节角 —— 改位姿，所有引用一起变。</p>
            <div className="insp__danger">
              <button
                type="button"
                className="danger"
                disabled={executing}
                onClick={() => {
                  onPatch(blocks.filter((b) => b.id !== block.id));
                  onClose();
                }}
              >
                删除保持块（位姿保留在库里）
              </button>
            </div>
          </>
        );
      }

      // transition
      return (
        <>
          <div className="insp__field">
            <span className="insp__label">时长</span>
            <Stepper
              value={block.duration_s}
              step={0.5}
              min={0.5}
              max={30}
              unit="s"
              disabled={executing}
              onChange={(v) => replaceBlock(block.id, { ...block, duration_s: v })}
            />
          </div>
          <div className="insp__field">
            <span className="insp__label">缓动</span>
            <div className="sheet__tiers" role="radiogroup" aria-label="缓动">
              {EASINGS.map((easing) => (
                <button
                  key={easing.value}
                  type="button"
                  role="radio"
                  aria-checked={block.easing === easing.value}
                  disabled={executing}
                  className={`sheet__tier ${block.easing === easing.value ? "selected" : ""}`}
                  onClick={() => replaceBlock(block.id, { ...block, easing: easing.value })}
                >
                  {easing.label}
                </button>
              ))}
            </div>
          </div>
          <div className="insp__field insp__path">
            <span className="insp__label">路径形态</span>
            <div className="sheet__tiers">
              <button type="button" className="sheet__tier selected" disabled>
                关节空间
              </button>
              <button type="button" className="sheet__tier" disabled title="后端能力待定">
                笛卡尔直线
              </button>
            </div>
          </div>
          <p className="hint">路径形态：后端能力待定，先占位。过渡块自动生成、不可删 —— 删掉两端的保持块会自动重接。</p>
        </>
      );
    }

    // marker
    const block = blocks.find((b) => b.id === selection.blockId);
    const marker = block?.markers.find((m) => m.id === selection.markerId);
    if (!block || !marker) return <p className="hint">所选标记已不存在。</p>;
    const provider = providers.find((p) => p.id === marker.kind);
    const isWait = marker.kind === WAIT_KIND;
    const setMarker = (next: typeof marker) =>
      replaceBlock(block.id, {
        ...block,
        markers: block.markers.map((m) => (m.id === marker.id ? next : m)),
      });

    return (
      <>
        <div className="insp__field">
          <span className="insp__label">位置</span>
          {block.type === "hold" ? (
            <Stepper
              value={marker.at}
              step={0.1}
              min={0}
              max={block.duration_s}
              unit="s"
              disabled={executing}
              onChange={(v) => setMarker({ ...marker, at: snap(clamp(v, 0, block.duration_s), 0.1) })}
            />
          ) : (
            <Stepper
              value={Math.round(marker.at * 100)}
              step={5}
              min={0}
              max={100}
              unit="%"
              disabled={executing}
              onChange={(v) => setMarker({ ...marker, at: clamp(v, 0, 100) / 100 })}
            />
          )}
        </div>

        {isWait ? (
          <p className="hint">等待标记：预演与执行走到这里都停住，手动点「继续」。开放式，不估时长。</p>
        ) : (
          <div className="insp__field">
            <span className="insp__label">预估</span>
            <Stepper
              value={marker.estimate_s}
              step={0.1}
              min={0.1}
              max={30}
              unit="s"
              disabled={executing}
              onChange={(v) => setMarker({ ...marker, estimate_s: v })}
            />
          </div>
        )}

        {provider?.installed && provider.fields.length > 0 ? (
          <ProviderFields
            fields={provider.fields}
            values={marker.params}
            disabled={executing || !provider.available}
            onChange={(key, value) =>
              setMarker({ ...marker, params: { ...marker.params, [key]: value } })
            }
          />
        ) : !isWait && !provider ? (
          <p className="hint">提供「{markerLabel(marker.kind, providers)}」的插件未安装 —— 标记保留，执行时按瞬发事件处理。</p>
        ) : null}

        {!isWait ? (
          <p className="hint">预估只做跨度显示：时间尺是指令尺，动作执行时长以实际为准。</p>
        ) : null}

        <div className="insp__danger">
          <button
            type="button"
            className="danger"
            disabled={executing}
            onClick={() => {
              replaceBlock(block.id, {
                ...block,
                markers: block.markers.filter((m) => m.id !== marker.id),
              });
              onClose();
            }}
          >
            删除标记
          </button>
        </div>
      </>
    );
  };

  const title =
    selection.kind === "block"
      ? blocks.find((b) => b.id === selection.id)?.type === "transition"
        ? "过渡块"
        : "保持块"
      : `标记 · ${markerLabel(
          blocks.find((b) => b.id === selection.blockId)?.markers.find((m) => m.id === selection.markerId)
            ?.kind ?? "",
          providers,
        )}`;

  return (
    <aside className="insp" aria-label="检查器">
      <div className="insp__head">
        <span className="insp__title">{title}</span>
        <button type="button" className="ghost insp__close" aria-label="关闭检查器" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="insp__body">{body()}</div>
    </aside>
  );
}

function Stepper({
  value,
  step,
  min,
  max,
  unit,
  disabled,
  onChange,
}: {
  value: number;
  step: number;
  min: number;
  max: number;
  unit: string;
  disabled: boolean;
  onChange: (value: number) => void;
}) {
  const commit = (next: number) => onChange(snap(clamp(next, min, max), step));
  return (
    <div className="sheet__stepper">
      <button
        type="button"
        className="sheet__stepper-btn"
        disabled={disabled || value <= min}
        aria-label="减少"
        onClick={() => commit(value - step)}
      >
        −
      </button>
      <span className="sheet__stepper-num num">
        {value.toFixed(step < 1 ? 1 : 0)}
        {unit}
      </span>
      <button
        type="button"
        className="sheet__stepper-btn"
        disabled={disabled || value >= max}
        aria-label="增加"
        onClick={() => commit(value + step)}
      >
        +
      </button>
    </div>
  );
}
