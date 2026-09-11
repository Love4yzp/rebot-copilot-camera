import type { Block, Pose, SeqPlayback, Sequence } from "../types";
import { makeMarker } from "./model";

interface Props {
  sequence: Sequence | null;
  poses: Pose[];
  playback: SeqPlayback | null;
  locked: boolean;
  onPatch: (blocks: Block[]) => void;
}

export function SequenceEditor({
  sequence,
  poses,
  playback,
  locked,
  onPatch,
}: Props) {
  if (!sequence)
    return (
      <p className="hint sequence-empty">
        先录下位姿，再新建序列，把位姿追加为站位。
      </p>
    );
  const blocks = sequence.blocks;
  const change = (id: string, patch: Partial<Block>) =>
    onPatch(
      blocks.map((b) => (b.id === id ? ({ ...b, ...patch } as Block) : b)),
    );
  const reorder = (id: string, direction: number) => {
    const order = blocks.filter((b) => b.type === "hold");
    const index = order.findIndex((b) => b.id === id),
      other = index + direction;
    if (other < 0 || other >= order.length) return;
    [order[index], order[other]] = [order[other], order[index]];
    let next = 0;
    onPatch(blocks.map((b) => (b.type === "hold" ? order[next++] : b)));
  };
  return (
    <div className="station-strip" aria-label="站位编排">
      {!blocks.length && <p className="hint">从位姿列表点「＋追加」开始。</p>}
      {blocks.map((block, index) => (
        <fieldset
          key={block.id + ":" + block.duration_s}
          disabled={locked}
          className={"station-editor " + block.type}
          data-current={
            playback?.sequence_id === sequence.id &&
            !playback.finished &&
            playback.block_index === index
          }
        >
          <legend>
            {block.type === "hold"
              ? (poses.find((p) => p.id === block.pose_id)?.name ??
                "位姿已删除")
              : "→ 过渡"}
          </legend>
          <label>
            {block.type === "hold" ? "保持" : "移动"}{" "}
            <input
              aria-label="时长（秒）"
              type="number"
              min={block.type === "hold" ? 0 : 0.1}
              max={600}
              step={0.1}
              defaultValue={block.duration_s}
              onBlur={(e) => {
                const value = Number(e.target.value);
                if (
                  e.target.value &&
                  Number.isFinite(value) &&
                  value >= (block.type === "hold" ? 0 : 0.1) &&
                  value <= 600 &&
                  value !== block.duration_s
                )
                  change(block.id, {
                    duration_s: value,
                    markers: block.markers.map((m) => ({
                      ...m,
                      at: Math.min(m.at, block.type === "hold" ? value : 1),
                    })),
                  });
              }}
            />{" "}
            s
          </label>
          {block.type === "transition" && (
            <p className="hint">轨迹由 SDK 按关节约束生成</p>
          )}
          <details>
            <summary>等待标记 ({block.markers.length})</summary>
            {block.markers.map((marker) => (
              <div key={marker.id + ":" + marker.at}>
                <label>
                  {block.type === "hold" ? "秒" : "比例"}
                  <input
                    type="number"
                    min={0}
                    max={block.type === "hold" ? block.duration_s : 1}
                    step={0.1}
                    defaultValue={marker.at}
                    onBlur={(e) => {
                      const at = Number(e.target.value);
                      if (
                        e.target.value &&
                        Number.isFinite(at) &&
                        at >= 0 &&
                        at <= (block.type === "hold" ? block.duration_s : 1)
                      )
                        change(block.id, {
                          markers: block.markers.map((m) =>
                            m.id === marker.id ? { ...m, at } : m,
                          ),
                        });
                    }}
                  />
                </label>
                <button
                  onClick={() =>
                    change(block.id, {
                      markers: block.markers.filter((m) => m.id !== marker.id),
                    })
                  }
                >
                  删除等待
                </button>
              </div>
            ))}
            <button
              onClick={() =>
                change(block.id, {
                  markers: [
                    ...block.markers,
                    makeMarker(
                      "wait",
                      block.type === "hold" ? block.duration_s : 0.5,
                      {},
                      0,
                    ),
                  ],
                })
              }
            >
              ＋等待
            </button>
          </details>
          {block.type === "hold" && (
            <div className="station-actions">
              <button onClick={() => reorder(block.id, -1)}>←</button>
              <button onClick={() => reorder(block.id, 1)}>→</button>
              <button
                onClick={() => onPatch(blocks.filter((b) => b.id !== block.id))}
              >
                移除站位
              </button>
            </div>
          )}
        </fieldset>
      ))}
    </div>
  );
}
