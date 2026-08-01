import { ArmView3D } from "./ArmView3D";

interface Props {
  open: boolean;
  onClose: () => void;
  positions: Record<string, number>;
  preview: Record<string, number> | null;
  /** Name of the anchor being previewed, so the drawer can say whose pose this is. */
  previewName: string | null;
}

/**
 * The 3D view, as a drawer.
 *
 * It used to hold a permanent 380 px column, which on an iPad in portrait
 * squeezed the anchor cards — the actual controls — into one cramped file. It
 * is a reference, not a control, so it gives the screen back and slides in
 * when asked.
 *
 * The preview is driven by selection, not hover: hover does not exist on the
 * device this runs on, and the old hover binding left a stale pose on screen
 * after the pointer moved away.
 */
export function ViewerDrawer({ open, onClose, positions, preview, previewName }: Props) {
  return (
    <aside className={`viewer-drawer${open ? " open" : ""}`} aria-hidden={!open}>
      <div className="viewer-drawer__head">
        <span className="engrave">{previewName ? `预览 · ${previewName}` : "实时姿态"}</span>
        <button className="ghost" onClick={onClose} tabIndex={open ? 0 : -1}>
          收起
        </button>
      </div>
      <ArmView3D positions={positions} preview={preview} />
    </aside>
  );
}
